"""tests/test_condor_breadth_gate_wf.py -- breadth gate on calm iron condors.

Pure gate-logic tests (no pricing, no IO). The harness around this (real BS
pricing + IS/OOS split) mirrors condor_in_trend_wf and is run, not unit-tested.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def _breadth():
    idx = pd.bdate_range("2024-01-01", periods=20)
    # starts high (80), deteriorates to 30 by the end
    vals = [80, 80, 78, 76, 74, 72, 70, 68, 60, 55, 50, 48, 45, 42, 40, 38, 36, 34, 32, 30]
    return pd.Series(vals, index=idx, dtype=float)


def test_gate_floor_keeps_only_high_breadth_days():
    from backtests.condor_breadth_gate_wf import gate_keep
    b = _breadth()
    dates = list(b.index)
    kept = gate_keep(dates, b, mode="floor", threshold=50.0)
    # only days with breadth >= 50 survive
    assert all(b.loc[d] >= 50.0 for d in kept)
    assert len(kept) == sum(1 for v in b.values if v >= 50.0)


def test_gate_not_falling_skips_deteriorating_days():
    from backtests.condor_breadth_gate_wf import gate_keep
    b = _breadth()
    dates = list(b.index)
    kept = gate_keep(dates, b, mode="not_falling", drop=15.0, window=8)
    # a day whose breadth fell >=15 vs 8 trading days ago must be skipped
    for d in dates:
        i = b.index.get_loc(d)
        if i >= 8 and b.iloc[i] <= b.iloc[i - 8] - 15.0:
            assert d not in kept


def test_gate_baseline_keeps_all():
    from backtests.condor_breadth_gate_wf import gate_keep
    b = _breadth()
    dates = list(b.index)
    assert gate_keep(dates, b, mode="none") == dates


def test_gate_skips_dates_without_breadth_data():
    from backtests.condor_breadth_gate_wf import gate_keep
    b = _breadth()
    dates = list(b.index) + [pd.Timestamp("2025-12-31")]  # no breadth for this one
    kept = gate_keep(dates, b, mode="floor", threshold=40.0)
    assert pd.Timestamp("2025-12-31") not in kept     # unknown breadth -> conservative skip
