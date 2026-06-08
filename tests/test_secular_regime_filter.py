"""tests/test_secular_regime_filter.py -- secular bull/bear filter for dip trades."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_secular_bull_flags_above_below_ma():
    from backtests.secular_regime_filter import secular_bull_flags
    idx = pd.bdate_range("2023-01-02", periods=260)
    close = pd.Series([float(100 + i) for i in range(260)], index=idx)   # steady uptrend
    spy = pd.DataFrame({"close": close})
    flags = secular_bull_flags(spy, [idx[-1]], ma_window=200)
    assert flags[idx[-1]] is True   # well above its rising 200d MA → secular bull


def test_split_by_secular_buckets_and_filter():
    from backtests.secular_regime_filter import split_by_secular
    trades = [
        {"entry_date": pd.Timestamp("2020-03-09"), "pnl_dollars": -300.0},
        {"entry_date": pd.Timestamp("2023-10-27"), "pnl_dollars": 400.0},
        {"entry_date": pd.Timestamp("2025-03-13"), "pnl_dollars": 500.0},
    ]
    flags = {pd.Timestamp("2020-03-09"): False,   # secular bear (the loser)
             pd.Timestamp("2023-10-27"): True,
             pd.Timestamp("2025-03-13"): True}
    res = split_by_secular(trades, flags)
    assert res["bear"]["n"] == 1 and res["bear"]["total_pnl"] == -300
    assert res["bull"]["n"] == 2 and res["bull"]["total_pnl"] == 900
    assert res["bull"]["mean_pnl"] > res["bear"]["mean_pnl"]   # filter (keep bull) helps
