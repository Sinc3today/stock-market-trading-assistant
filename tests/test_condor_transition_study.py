"""tests/test_condor_transition_study.py -- transition-zone condor sub-condition."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_vix_direction_flags_rising():
    from backtests.condor_transition_study import vix_direction
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "vix":  [15.0, 16.0, 17.0, 18.0],
    })
    d = vix_direction(df, lookback=2)
    # row 3 (18) vs row 1 (16) → rising; row 2 (17) vs row 0 (15) → rising
    assert bool(d["vix_rising"].iloc[2]) is True
    assert bool(d["vix_rising"].iloc[3]) is True


def test_transition_subcondition_splits_by_vol_direction():
    from backtests.condor_transition_study import transition_subcondition
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-06-01", "2025-01-01", "2025-06-01"]),
        "regime": ["choppy_transition"] * 4,
        "tradeable": [True] * 4,
        "vix_rising": [True, False, True, False],
        "outcome": ["loss", "win", "loss", "win"],
        "pnl": [-200.0, 150.0, -300.0, 120.0],
    })
    res = transition_subcondition(df)
    assert res["vix_rising"]["n"] == 2 and res["vix_rising"]["pnl"] == -500
    assert res["vix_falling"]["n"] == 2 and res["vix_falling"]["pnl"] == 270
    assert res["vix_rising"]["pnl"] < res["vix_falling"]["pnl"]   # losses cluster in rising
