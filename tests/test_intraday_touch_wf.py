import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backtests.intraday_touch_wf import compare_runs, split_oos
from backtests.intraday_touch_wf import PRESETS, evaluate_preset


def _trades_off():
    return pd.DataFrame([
        {"date": pd.Timestamp("2022-03-01"), "regime": "trending_up_calm",  "pnl_dollars": +100.0, "exit_reason": "target"},
        {"date": pd.Timestamp("2022-06-01"), "regime": "choppy_low_vol",    "pnl_dollars": -50.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2024-01-15"), "regime": "trending_up_calm",  "pnl_dollars": +80.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2025-02-01"), "regime": "choppy_low_vol",    "pnl_dollars": +30.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2025-09-01"), "regime": "trending_up_calm",  "pnl_dollars": -120.0, "exit_reason": "expiry"},
    ])


def _trades_on():
    # Same entry dates and regimes, but intraday-touch caught two peaks earlier.
    return pd.DataFrame([
        {"date": pd.Timestamp("2022-03-01"), "regime": "trending_up_calm",  "pnl_dollars": +100.0, "exit_reason": "target"},
        {"date": pd.Timestamp("2022-06-01"), "regime": "choppy_low_vol",    "pnl_dollars": -50.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2024-01-15"), "regime": "trending_up_calm",  "pnl_dollars": +160.0, "exit_reason": "target_intraday"},
        {"date": pd.Timestamp("2025-02-01"), "regime": "choppy_low_vol",    "pnl_dollars": +30.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2025-09-01"), "regime": "trending_up_calm",  "pnl_dollars": +50.0,  "exit_reason": "target_intraday"},
    ])


def test_split_oos_by_date_fraction():
    """First 60% of entries by date count = in-sample, rest = OOS."""
    df = _trades_off()
    ins, oos = split_oos(df, fraction=0.6)
    assert len(ins) == 3 and len(oos) == 2
    assert ins["date"].max() < oos["date"].min()


def test_compare_runs_computes_deltas_attribution_and_per_regime():
    off, on = _trades_off(), _trades_on()
    result = compare_runs(off, on, oos_fraction=0.6)
    # Delta is per-trade mean of (on - off) on identical entry dates.
    # IS rows (first 3 entries): deltas = [0, 0, +80]; mean = 80/3 ≈ 26.67
    assert abs(result["is_delta_per_trade"] - (80.0 / 3)) < 0.01
    # OOS rows (last 2): deltas = [0, +170]; mean = 85.0
    assert abs(result["oos_delta_per_trade"] - 85.0) < 0.01
    # OOS baseline: (-120 + 30) / 2 = -45.0
    assert abs(result["oos_baseline_per_trade"] - (-45.0)) < 0.01
    # Attribution: 1 of 2 OOS exits in `on` is target_intraday => 0.5
    assert abs(result["oos_attribution"] - 0.5) < 0.01
    # Per-regime: trending_up_calm has 1 IS + 2 OOS = 3 in this slice
    rmap = result["per_regime"]
    assert "trending_up_calm" in rmap
    assert rmap["trending_up_calm"]["n"] == 3


def _measured(dollar, baseline_dollar, attrib, is_delta):
    """Build a `measured` dict shaped like compare_runs() output."""
    return {
        "oos_delta_per_trade":    dollar,
        "oos_baseline_per_trade": baseline_dollar,
        "oos_attribution":        attrib,
        "is_delta_per_trade":     is_delta,
        "n_oos": 230,
    }


def test_evaluate_preset_strict_passes_only_on_large_signal():
    strict = next(p for p in PRESETS if p["name"] == "strict-3σ")
    weak   = _measured(dollar=30.0, baseline_dollar=100.0, attrib=0.30, is_delta=20.0)
    strong = _measured(dollar=50.0, baseline_dollar=100.0, attrib=0.40, is_delta=30.0)
    assert evaluate_preset(weak,   strict)["ship"] is False  # below $40 stat floor
    assert evaluate_preset(strong, strict)["ship"] is True


def test_evaluate_preset_default_uses_config_constants():
    """default-2σ floors come from config.INTRADAY_TOUCH_SHIP_MIN_* so they're tunable."""
    import config
    default = next(p for p in PRESETS if p["name"] == "default-2σ")
    assert default["stat_floor"]   == config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR
    assert default["scale_floor"]  == config.INTRADAY_TOUCH_SHIP_MIN_FRAC
    assert default["attrib_floor"] == config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB


def test_evaluate_preset_blocks_when_is_sanity_fails():
    """If is_sanity is on and IS delta is non-positive, the preset must NOT ship
    even if all dollar/scale/attribution floors pass."""
    default = next(p for p in PRESETS if p["name"] == "default-2σ")
    passes_others = _measured(dollar=50.0, baseline_dollar=100.0, attrib=0.40, is_delta=-5.0)
    out = evaluate_preset(passes_others, default)
    assert out["ship"] is False
    assert out["floors_met"]["is_sanity"] is False


def test_evaluate_preset_oos_only_ignores_is_direction():
    """oos-only preset has is_sanity=off; a negative IS delta must not block ship."""
    oos_only = next(p for p in PRESETS if p["name"] == "oos-only")
    m = _measured(dollar=50.0, baseline_dollar=100.0, attrib=0.40, is_delta=-5.0)
    assert evaluate_preset(m, oos_only)["ship"] is True


def test_evaluate_preset_attribution_strict_requires_30pct_attrib():
    attrib_strict = next(p for p in PRESETS if p["name"] == "attribution-strict")
    low_attrib  = _measured(dollar=30.0, baseline_dollar=100.0, attrib=0.20, is_delta=10.0)
    high_attrib = _measured(dollar=30.0, baseline_dollar=100.0, attrib=0.35, is_delta=10.0)
    assert evaluate_preset(low_attrib,  attrib_strict)["ship"] is False
    assert evaluate_preset(high_attrib, attrib_strict)["ship"] is True


def test_presets_table_has_exactly_six_entries():
    names = {p["name"] for p in PRESETS}
    assert names == {"strict-3σ", "default-2σ", "lenient-1.5σ",
                     "research-1σ", "attribution-strict", "oos-only"}
