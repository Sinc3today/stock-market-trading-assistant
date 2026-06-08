"""tests/test_overbought_short_study.py -- overbought-short signal study (mirror of dip-buy)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_overbought_triggers_fresh_cross_above():
    from backtests.overbought_short_study import overbought_triggers
    rsi = pd.Series([65, 72, 73, 69, 71], dtype=float)
    trig = overbought_triggers(rsi, threshold=70)
    assert list(trig) == [False, True, False, False, True]


def test_run_arm_short_reports_forward_and_short_edge():
    from backtests.overbought_short_study import run_overbought
    # rising then a fresh RSI>70 then a DROP → SPY reverts down → short profits
    closes = list(range(100, 130)) + [131, 129, 127, 125, 123, 121, 119, 117]
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    df = pd.DataFrame({"close": [float(c) for c in closes]}, index=idx)
    res = run_overbought(df)
    assert set(res.keys()) >= {"by_horizon"}
    for h in (3, 5, 10):
        assert h in res["by_horizon"]
        assert {"cond_mean", "short_edge", "pct_down"} <= set(res["by_horizon"][h])
