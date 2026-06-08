"""tests/test_condor_breach_study.py -- calm-condor breach-prediction study."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_breach_split_separates_by_signal():
    from backtests.condor_breach_study import breach_split
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-03", "2024-06-03", "2025-01-03", "2025-06-03"]),
        "regime": ["choppy_low_vol"] * 4,
        "tradeable": [True] * 4,
        "outcome": ["loss", "win", "loss", "win"],
        "pnl": [-200.0, 150.0, -200.0, 150.0],
        "sig": [True, False, True, False],   # signal flags both losers
    })
    res = breach_split(df, "sig")
    assert res["sig_true"]["n"] == 2 and res["sig_true"]["win_rate"] == 0.0
    assert res["sig_false"]["n"] == 2 and res["sig_false"]["win_rate"] == 1.0
    # skipping signal-true days lifts win rate to 100% and removes the losses
    assert res["sig_false"]["win_rate"] > res["sig_true"]["win_rate"]


def test_breach_split_ignores_non_calm_condor():
    from backtests.condor_breach_study import breach_split
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-03", "2024-06-03"]),
        "regime": ["choppy_low_vol", "trending_up_calm"],
        "tradeable": [True, True],
        "outcome": ["loss", "loss"],
        "pnl": [-200.0, -300.0],
        "sig": [True, True],
    })
    res = breach_split(df, "sig")
    assert res["sig_true"]["n"] == 1   # only the calm-condor row counts
