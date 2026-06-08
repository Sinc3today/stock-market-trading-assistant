"""tests/test_dipbuy_breakdown_study.py -- 50d-low breakdown as a 2nd dip-buy trigger."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_breakdown_triggers_fire_on_new_low():
    from backtests.dipbuy_breakdown_study import breakdown_triggers
    close = pd.Series([100.0] * 10 + [95.0, 94.0])
    df = pd.DataFrame({"close": close})
    trig = breakdown_triggers(df, window=10)
    assert bool(trig.iloc[10]) is True       # fresh break below the prior 10d low
    assert not bool(trig.iloc[11])


def test_trigger_overlap_counts_shared_and_near():
    from backtests.dipbuy_breakdown_study import trigger_overlap
    idx = pd.bdate_range("2024-01-01", periods=10)
    a = pd.Series([False] * 10, index=idx); a.iloc[2] = True; a.iloc[7] = True
    b = pd.Series([False] * 10, index=idx); b.iloc[2] = True; b.iloc[8] = True
    ov = trigger_overlap(a, b, within=2)
    assert ov["a_n"] == 2 and ov["b_n"] == 2
    assert ov["same_day"] == 1                # idx 2 shared exactly
    assert ov["within"] >= 1                  # idx 7 (a) within 2d of idx 8 (b)
