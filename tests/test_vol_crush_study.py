"""tests/test_vol_crush_study.py -- post vol-crush behavior study."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_crush_events_fire_on_elevated_drop():
    from backtests.vol_crush_study import crush_events
    idx = pd.bdate_range("2024-01-01", periods=10)
    vix = pd.Series([30, 31, 30, 29, 30, 30, 24, 22, 20, 19], index=idx, dtype=float)
    ev = crush_events(vix, drop_pct=-0.10, min_prior=20.0, window=2)
    assert bool(ev.iloc[6]) is True     # 24 vs 30 (2d ago) = -20%, was elevated → crush
    assert not bool(ev.iloc[0])         # no prior data


def test_crush_events_ignore_drop_from_low_vix():
    from backtests.vol_crush_study import crush_events
    idx = pd.bdate_range("2024-01-01", periods=6)
    vix = pd.Series([15, 15, 15, 13, 12, 11], index=idx, dtype=float)  # low, not elevated
    ev = crush_events(vix, drop_pct=-0.10, min_prior=20.0, window=2)
    assert not ev.any()                 # drop from low VIX is not a "crush"


def test_post_crush_window_metrics():
    from backtests.vol_crush_study import post_crush_window
    idx = pd.bdate_range("2024-01-01", periods=12)
    close = pd.Series([100, 100, 100, 100, 100, 105, 106, 107, 108, 109, 110, 111],
                      index=idx, dtype=float)
    spy = pd.DataFrame({"close": close})
    res = post_crush_window(spy, [idx[5]], horizon=5)
    assert res["n"] == 1
    assert res["fwd_mean"] > 0          # relief rally after the crush
    assert "fwd_realized_vol" in res and "baseline_realized_vol" in res
