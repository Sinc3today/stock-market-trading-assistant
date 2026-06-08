"""tests/test_dipbuy_breadth_confirm_wf.py -- breadth-washout confirmer on the
oversold dip-buy. Pure mask-logic test (no pricing, no IO)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def test_washout_confirm_keeps_only_low_breadth_triggers():
    from backtests.dipbuy_breadth_confirm_wf import washout_confirm
    idx = pd.bdate_range("2024-01-01", periods=6)
    triggers = pd.Series([True, True, False, True, True, False], index=idx)
    breadth = pd.Series([10, 50, 5, 18, 70, 8], index=idx, dtype=float)
    out = washout_confirm(triggers, breadth, max_breadth=20.0)
    # True only where trigger AND breadth<=20: idx0 (10), idx3 (18)
    assert list(out.values) == [True, False, False, True, False, False]


def test_washout_confirm_excludes_missing_breadth():
    from backtests.dipbuy_breadth_confirm_wf import washout_confirm
    idx = pd.bdate_range("2024-01-01", periods=4)
    triggers = pd.Series([True, True, True, True], index=idx)
    breadth = pd.Series([10, float("nan"), 5, 15], index=idx, dtype=float)
    out = washout_confirm(triggers, breadth, max_breadth=20.0)
    assert list(out.values) == [True, False, True, True]   # NaN day dropped


def test_washout_confirm_returns_boolean_series_aligned_to_triggers():
    from backtests.dipbuy_breadth_confirm_wf import washout_confirm
    idx = pd.bdate_range("2024-01-01", periods=3)
    triggers = pd.Series([False, True, True], index=idx)
    breadth = pd.Series([5, 5, 5], index=idx, dtype=float)
    out = washout_confirm(triggers, breadth, max_breadth=20.0)
    assert list(out.index) == list(idx)
    assert out.dtype == bool
    assert int(out.sum()) == 2
