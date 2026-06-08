"""tests/test_trend_follow_study.py -- Donchian trend-follow signal study."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_donchian_breakout_up_fresh_cross():
    from backtests.trend_follow_study import donchian_breakout
    # flat 100 for 10 bars, then 105 (breaks the prior 10d high), then stays up
    close = pd.Series([100.0] * 10 + [105.0, 106.0, 107.0])
    up = donchian_breakout(close, window=10, direction="up")
    assert up.iloc[10] is True or bool(up.iloc[10]) is True   # fresh break at idx 10
    assert not bool(up.iloc[11])                              # not fresh (already above)


def test_donchian_breakdown_down_fresh_cross():
    from backtests.trend_follow_study import donchian_breakout
    close = pd.Series([100.0] * 10 + [95.0, 94.0, 93.0])
    dn = donchian_breakout(close, window=10, direction="down")
    assert bool(dn.iloc[10]) is True
    assert not bool(dn.iloc[11])


def test_run_trend_arm_structure():
    from backtests.trend_follow_study import run_trend_arm
    import numpy as np
    idx = pd.bdate_range("2015-01-02", periods=400)
    close = pd.Series(400 + np.cumsum(np.random.default_rng(0).normal(0.1, 1.0, 400)),
                      index=idx)
    df = pd.DataFrame({"close": close})
    res = run_trend_arm(df, direction="up", window=50)
    for h in (10, 20):
        assert h in res["by_horizon"]
        assert {"cond_mean", "baseline_mean", "edge", "n"} <= set(res["by_horizon"][h])
