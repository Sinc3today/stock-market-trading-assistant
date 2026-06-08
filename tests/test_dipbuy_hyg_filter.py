"""tests/test_dipbuy_hyg_filter.py -- HYG risk-off filter for the oversold dip-buy."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_hyg_risk_off_flags_below_ma():
    from backtests.dipbuy_hyg_filter import hyg_risk_off_flags
    # rising then a sharp drop below the MA at the end
    idx = pd.bdate_range("2020-01-01", periods=80)
    vals = list(range(80, 140)) + [120, 110, 100, 95, 90, 88, 86, 84, 82, 80,
                                   78, 76, 74, 72, 70, 68, 66, 64, 62, 60]
    hyg = pd.Series([float(v) for v in vals], index=idx)
    flags = hyg_risk_off_flags(hyg, [idx[-1], idx[55]], ma_window=50)
    assert flags[idx[-1]] is True     # crashed well below its 50d MA → risk-off
    assert flags[idx[55]] is False    # still rising above MA → not risk-off


def test_split_trades_by_hyg():
    from backtests.dipbuy_hyg_filter import split_trades_by_hyg
    trades = [
        {"entry_date": pd.Timestamp("2020-03-09"), "pnl_dollars": -500.0},
        {"entry_date": pd.Timestamp("2021-05-03"), "pnl_dollars": 300.0},
        {"entry_date": pd.Timestamp("2023-10-27"), "pnl_dollars": 200.0},
    ]
    flags = {pd.Timestamp("2020-03-09"): True,   # risk-off (falling knife)
             pd.Timestamp("2021-05-03"): False,
             pd.Timestamp("2023-10-27"): False}
    res = split_trades_by_hyg(trades, flags)
    assert res["risk_off"]["n"] == 1 and res["risk_off"]["total_pnl"] == -500
    assert res["ok"]["n"] == 2 and res["ok"]["total_pnl"] == 500
    assert res["ok"]["mean_pnl"] > res["risk_off"]["mean_pnl"]   # filter helps
