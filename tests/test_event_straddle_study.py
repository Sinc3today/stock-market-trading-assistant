"""tests/test_event_straddle_study.py -- direction-agnostic event-day test.

Does the realized move beat the pre-event straddle cost (long-straddle edge),
fall short of it (IV-crush / short-premium edge), or wash (efficient = dead)?
Pure-logic tests; the real option-price pull is exercised in main(), not here.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_nearest_friday_on_or_after():
    from backtests.event_straddle_study import nearest_friday_on_or_after
    assert nearest_friday_on_or_after(date(2025, 3, 19)) == date(2025, 3, 21)  # Wed -> Fri
    assert nearest_friday_on_or_after(date(2025, 3, 21)) == date(2025, 3, 21)  # Fri -> same
    assert nearest_friday_on_or_after(date(2025, 3, 22)) == date(2025, 3, 28)  # Sat -> next Fri


def test_nfp_dates_are_first_fridays():
    from backtests.event_straddle_study import nfp_dates
    d = nfp_dates(date(2025, 1, 1), date(2025, 3, 31))
    assert d == [date(2025, 1, 3), date(2025, 2, 7), date(2025, 3, 7)]


def test_straddle_outcome_long_wins_when_move_exceeds_cost():
    from backtests.event_straddle_study import straddle_outcome
    o = straddle_outcome(strike=600.0, cost=5.0, exit_spot=610.0)   # move 10 > cost 5
    assert o["move"] == 10.0
    assert o["long_win"] is True
    assert o["pnl_long"] == 5.0
    assert o["pnl_short"] == -5.0


def test_straddle_outcome_short_wins_when_move_below_cost():
    from backtests.event_straddle_study import straddle_outcome
    o = straddle_outcome(strike=600.0, cost=12.0, exit_spot=603.0)  # move 3 < cost 12
    assert o["long_win"] is False
    assert o["pnl_long"] == -9.0
    assert o["pnl_short"] == 9.0


def test_summarize_aggregates_both_sides():
    from backtests.event_straddle_study import summarize
    outs = [
        {"pnl_long": 5.0, "long_win": True},
        {"pnl_long": -9.0, "long_win": False},
        {"pnl_long": -3.0, "long_win": False},
    ]
    s = summarize(outs)
    assert s["n"] == 3
    assert s["long_winrate"] == pytest.approx(33.3, abs=0.1)
    # long mean = (5-9-3)/3 = -2.333; short is the mirror
    assert s["long_mean"] == pytest.approx(-2.33, abs=0.01)
    assert s["short_mean"] == pytest.approx(2.33, abs=0.01)
