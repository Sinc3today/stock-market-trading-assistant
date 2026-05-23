import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backtests.intraday_touch_wf import compare_runs, split_oos


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
