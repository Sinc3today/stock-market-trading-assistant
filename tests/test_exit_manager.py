"""tests/test_exit_manager.py -- tests for exit_rule_for public accessor."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.exit_manager import exit_rule_for


def test_exit_rule_for_exposes_time_exit_keys():
    rule = exit_rule_for("put_debit_spread", "0DTE")
    assert "scratch_time" in rule
    assert "scratch_theta" in rule
    assert "hard_close_time" in rule
    assert rule["scratch_time"] is None
    assert rule["hard_close_time"] is None


# ── 0DTE intraday time value (audit B1) ───────────────────────────
# _mark_exit_price used whole-day DTE, so at dte=0 t_years collapsed to 0 and
# bs_price fell back to pure intrinsic. Every 0DTE spread marked at its
# intrinsic value, instantly showed max loss, and tripped the stop within
# minutes of entry. 18 live records were destroyed this way.
# Real case: a 758/761 call debit spread bought for $0.78 with SPY at 757.82
# and ~5.5h to run was worth ~$0.89 — it marked $0.00.

from datetime import date, datetime, timedelta

import pytz

from learning.exit_manager import ExitManager, _years_to_expiry

_ET = pytz.timezone("US/Eastern")


def _mid_session(day: date, hour: int = 10, minute: int = 20):
    return _ET.localize(datetime(day.year, day.month, day.day, hour, minute))


def test_years_to_expiry_keeps_intraday_time_on_expiry_day():
    today = date(2026, 6, 2)
    t = _years_to_expiry(today, today, now=_mid_session(today))
    assert t > 0, "0DTE must retain the hours left before the 16:00 close"
    assert t < 1 / 365, "but less than a full day"


def test_years_to_expiry_shrinks_as_the_session_runs():
    today = date(2026, 6, 2)
    morning = _years_to_expiry(today, today, now=_mid_session(today, 10))
    afternoon = _years_to_expiry(today, today, now=_mid_session(today, 15))
    assert morning > afternoon > 0


def test_years_to_expiry_counts_whole_days_ahead():
    today = date(2026, 6, 2)
    t = _years_to_expiry(today + timedelta(days=7), today, now=_mid_session(today))
    assert 6 / 365 < t < 8 / 365


def test_years_to_expiry_is_never_negative_after_the_close():
    today = date(2026, 6, 2)
    assert _years_to_expiry(today, today, now=_mid_session(today, 17)) >= 0.0


def test_zero_dte_spread_is_not_marked_at_intrinsic():
    """The exact record that broke: must mark near its real ~$0.89, not $0.00."""
    legs = [{"action": "BUY", "option_type": "call", "strike": 758},
            {"action": "SELL", "option_type": "call", "strike": 761}]
    em = ExitManager.__new__(ExitManager)
    today = date(2026, 6, 2)
    mark = em._mark_exit_price("call_debit_spread", legs, 757.82, 16.1,
                               today, 0, now=_mid_session(today))
    assert mark > 0.40, f"0DTE spread marked at {mark}, time value discarded"


def test_deep_otm_spread_near_the_close_still_marks_near_zero():
    """The fix must not invent value where there genuinely is none."""
    legs = [{"action": "BUY", "option_type": "call", "strike": 800},
            {"action": "SELL", "option_type": "call", "strike": 803}]
    em = ExitManager.__new__(ExitManager)
    today = date(2026, 6, 2)
    mark = em._mark_exit_price("call_debit_spread", legs, 757.82, 16.1,
                               today, 0, now=_mid_session(today, 15, 55))
    assert mark <= 0.05


def test_stop_does_not_fire_at_a_zero_mark():
    """Defense in depth: a stop is a PARTIAL loss. A $0.00 mark means the
    position is worthless, which is the expiry resolver's job, not a stop."""
    em = ExitManager.__new__(ExitManager)
    today = date(2026, 6, 2)
    trade = {"strategy": "call_debit_spread", "dte_bucket": "0DTE",
             "entry_price": 0.78, "size": 1, "max_profit": 222.0,
             "max_loss": 78.0,
             "legs": [{"action": "BUY", "option_type": "call", "strike": 800,
                       "expiry": "2026-06-02"},
                      {"action": "SELL", "option_type": "call", "strike": 803,
                       "expiry": "2026-06-02"}]}
    out = em._evaluate(trade, 757.82, 16.1, today, now=_mid_session(today, 15, 59))
    assert out is None or out[0] > 0, "must not close a position at a $0.00 fill"
