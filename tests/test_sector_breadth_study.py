"""tests/test_sector_breadth_study.py -- market-breadth-as-risk-signal study.

Pure-function tests (no file IO, no network). Validates the breadth proxy
(% of sectors above their own MA) and the conditional forward-return engine.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def test_pct_above_ma_basic():
    from backtests.sector_breadth_study import pct_above_ma
    idx = pd.bdate_range("2024-01-01", periods=5)
    panel = {
        "A": pd.Series([10, 10, 10, 10, 20], index=idx, dtype=float),  # last day above its MA
        "B": pd.Series([20, 20, 20, 20, 10], index=idx, dtype=float),  # last day below its MA
    }
    pct = pct_above_ma(panel, window=3)
    assert round(pct.iloc[4], 1) == 50.0     # 1 of 2 sectors above MA


def test_pct_above_ma_excludes_sectors_without_valid_ma():
    from backtests.sector_breadth_study import pct_above_ma
    idx = pd.bdate_range("2024-01-01", periods=5)
    panel = {
        "A": pd.Series([1, 2, 3, 4, 5], index=idx, dtype=float),       # rising, valid MA from idx1
        "B": pd.Series([float("nan")] * 5, index=idx, dtype=float),     # never has a valid MA
    }
    pct = pct_above_ma(panel, window=2)
    # B is excluded from the denominator entirely; A is above its rising MA
    assert round(pct.iloc[1], 1) == 100.0


def test_conditional_forward_rising_market():
    from backtests.sector_breadth_study import conditional_forward
    idx = pd.bdate_range("2024-01-01", periods=12)
    close = pd.Series(range(100, 112), index=idx, dtype=float)  # steadily rising
    spy = pd.DataFrame({"close": close})
    res = conditional_forward(spy, [idx[2]], horizon=5)
    assert res["n"] == 1
    assert res["fwd_mean"] > 0
    assert "fwd_vol" in res and "baseline_vol" in res
    assert "baseline_fwd" in res


def test_conditional_forward_skips_out_of_range_dates():
    from backtests.sector_breadth_study import conditional_forward
    idx = pd.bdate_range("2024-01-01", periods=6)
    close = pd.Series(range(100, 106), index=idx, dtype=float)
    spy = pd.DataFrame({"close": close})
    # last date has no room for a 5-day forward window -> skipped
    res = conditional_forward(spy, [idx[5]], horizon=5)
    assert res["n"] == 0
