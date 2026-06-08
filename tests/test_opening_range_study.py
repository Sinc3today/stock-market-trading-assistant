"""tests/test_opening_range_study.py -- 0DTE opening-range signal study (Phase 1)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def _et_day(closes, day="2025-03-03"):
    """Build a 5-min ET-indexed session (09:30 onward) from a list of closes."""
    idx = pd.date_range(f"{day} 09:30", periods=len(closes), freq="5min", tz="US/Eastern")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5,
                         "close": c, "volume": 1_000_000.0})


def test_daily_features_breakout_up():
    from backtests.opening_range_study import daily_features
    # OR (first 3 bars 9:30/9:35/9:40) flat ~100 → or_high≈100.5; 9:45 closes 102
    # (breaks up); then rallies to 110 → rest_return positive.
    closes = [100, 100, 100, 102] + [104, 106, 108, 110]
    f = daily_features(_et_day(closes), prior_close=99.0)
    assert f["break_up"] is True and f["break_down"] is False
    assert f["rest_return"] > 0
    assert f["gap_up"] is True   # open 100 > prior 99


def test_daily_features_breakout_down():
    from backtests.opening_range_study import daily_features
    closes = [100, 100, 100, 98] + [96, 95, 94, 93]
    f = daily_features(_et_day(closes), prior_close=101.0)
    assert f["break_down"] is True and f["break_up"] is False
    assert f["rest_return"] < 0
    assert f["gap_down"] is True


def test_daily_features_vwap_arm():
    from backtests.opening_range_study import daily_features
    closes = [100, 100, 100, 102] + [104, 106, 108, 110]
    f = daily_features(_et_day(closes), prior_close=99.0)
    # decision close 102 > session VWAP (~100.5) → above_vwap → break_up_vwap
    assert f["above_vwap"] is True
    assert f["break_up_vwap"] is True


def test_daily_features_respects_or_minutes():
    from backtests.opening_range_study import daily_features
    # 30-min OR = first 6 bars; bars 0-5 flat 100 (or_high≈100.5), bar6 (10:00)=103
    closes = [100, 100, 100, 100, 100, 100, 103, 105, 107]
    f = daily_features(_et_day(closes), prior_close=99.0, or_minutes=30)
    assert f["break_up"] is True       # 103 > 30-min OR high
    assert f["rest_return"] > 0


def test_vix_gate_splits_edges_by_bucket():
    from backtests.opening_range_study import vix_gate
    idx = pd.to_datetime(["2024-01-03", "2024-02-03", "2024-03-03", "2024-04-03"])
    table = pd.DataFrame({
        "rest_return": [1.0, 0.5, 2.0, 1.5],
        "break_up":    [True, True, True, True],
        "vix":         [12.0, 14.0, 25.0, 30.0],   # 2 calm, 2 high
    }, index=idx)
    res = vix_gate(table, "break_up", threshold=18.0)
    assert res["calm"]["n"] == 2 and res["high"]["n"] == 2
    assert res["high"]["cond_mean"] > res["calm"]["cond_mean"]  # bigger moves on high-vol


def test_sweep_or_windows_structure():
    from backtests.opening_range_study import sweep_or_windows
    # tiny 2-day intraday frame is enough to exercise the structure
    bars = []
    for day in ("2025-03-03", "2025-03-04"):
        idx = pd.date_range(f"{day} 09:30", periods=12, freq="5min", tz="US/Eastern")
        c = pd.Series(range(100, 112), index=idx, dtype=float)
        bars.append(pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5,
                                  "close": c, "volume": 1e6}))
    df = pd.concat(bars).tz_convert("UTC").tz_localize(None)
    res = sweep_or_windows(df, windows=(5, 15), arms=("break_up",))
    assert set(res.keys()) == {5, 15}
    assert "break_up" in res[5] and "cond_mean" in res[5]["break_up"]


def test_run_arm_reuses_edge_machinery():
    from backtests.opening_range_study import run_arm
    # per-day table: rest_return + condition columns, indexed by date
    idx = pd.to_datetime(["2024-01-03", "2024-06-03", "2025-01-03", "2025-06-03"])
    table = pd.DataFrame({
        "rest_return": [1.0, 1.5, -0.5, 0.8],
        "break_up":    [True, True, False, True],
    }, index=idx)
    res = run_arm(table, "break_up")
    assert res["arm"] == "break_up"
    assert res["n"] == 3
    assert {"cond_mean", "baseline_mean", "edge", "pct_positive", "per_year"} <= set(res)
