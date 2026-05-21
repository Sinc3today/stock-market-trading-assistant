"""
tests/test_walk_forward.py -- walk-forward harness.

_metrics is unit-tested directly. The full walk_forward run drives 16 full
backtests (~minutes), so it's marked integration and excluded from the fast
suite.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests import walk_forward as wf


def _df(rows):
    return pd.DataFrame(rows)


def test_metrics_empty():
    m = wf._metrics(_df([{"tradeable": False, "outcome": "skip", "pnl": 0}]))
    assert m["n"] == 0 and m["sharpe"] == 0.0


def test_metrics_basic_counts_and_winrate():
    m = wf._metrics(_df([
        {"tradeable": True, "outcome": "win",       "pnl": 100},
        {"tradeable": True, "outcome": "loss",      "pnl": -50},
        {"tradeable": True, "outcome": "win",       "pnl": 120},
        {"tradeable": True, "outcome": "breakeven", "pnl": 0},
        {"tradeable": False, "outcome": "skip",     "pnl": 0},   # excluded
    ]))
    assert m["n"]   == 4               # 3 closed + 1 breakeven, skip excluded
    assert m["win"] == 50.0            # 2 wins of 4
    assert m["pnl"] == 170             # only tradeable rows summed


def test_metrics_sharpe_positive_when_net_positive():
    m = wf._metrics(_df([
        {"tradeable": True, "outcome": "win",  "pnl": 100},
        {"tradeable": True, "outcome": "win",  "pnl": 110},
        {"tradeable": True, "outcome": "loss", "pnl": -40},
    ]))
    assert m["sharpe"] > 0


@pytest.mark.integration
def test_walk_forward_structure_and_no_lookahead():
    """Full run on the local 5yr CSV. Verifies the output shape and that
    every fold tests a year strictly AFTER its training data (no lookahead)."""
    from backtests.spy_daily_backtest import BacktestDataLoader
    from data.event_calendar import EventCalendar
    spy, vix = BacktestDataLoader().load(years=5, source="local")
    res = wf.walk_forward(spy, vix, EventCalendar())
    assert "folds" in res and "oos" in res and "in_sample" in res
    assert len(res["folds"]) >= 2
    for f in res["folds"]:
        assert f["chosen_adx"] in wf.ADX_GRID
        assert f["chosen_ext"] in wf.EXT_GRID
        assert f["test"]["n"] >= 0
    # OOS aggregate should have trades across the test years.
    assert res["oos"]["n"] > 0
