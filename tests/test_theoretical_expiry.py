"""tests/test_theoretical_expiry.py -- an unpriced play must still be dated,
and must still look unpriced.

On 2026-09-08 the options chain refused to build a condor ("condor has an
unpriced leg -- no structure"), which is Phase 0 discipline working as
designed: a missing price is not a price. The system then fell back to
theoretical legs and published them as the day's play -- strikes, a "$300 max
profit", and "Expiration: ~45 days from now". Three problems in one card:

  * No expiry DATE, so the dashboard's "Exp" line rendered nothing. This was
    the reported bug.
  * Nothing said the play was unpriced. A model estimate and a real $1.84
    credit are indistinguishable once rendered.
  * The DTE label is the REQUESTED dte, printed next to whatever expiry was
    actually chosen. Across 6 recent briefs those disagreed by -7 to +5 days.

The rule is not "make a number appear". It is: give the date we are actually
targeting, and say plainly that nothing about it has been priced.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.options_layer import OptionsLayer


def _theoretical_legs():
    return [{"action": "sell", "option_type": "put", "strike": 739.0},
            {"action": "buy", "option_type": "put", "strike": 734.0}]


def test_theoretical_legs_get_a_target_expiration_date():
    """The reported bug: no date at all on an unpriced play."""
    legs = OptionsLayer._date_theoretical_legs(_theoretical_legs(), dte=45,
                                               today=date(2026, 9, 8))
    assert all(l.get("expiration") for l in legs)


def test_the_target_date_uses_the_existing_expiry_owner():
    """condor_calc._nearest_friday already owns 'which Friday does this DTE
    land on'. A seventh time-to-expiry convention is how we got here."""
    from signals.condor_calc import _nearest_friday
    legs = OptionsLayer._date_theoretical_legs(_theoretical_legs(), dte=45,
                                               today=date(2026, 9, 8))
    expected = _nearest_friday(date(2026, 9, 8) + timedelta(days=45))
    assert legs[0]["expiration"] == expected.isoformat()


def test_a_target_expiration_is_flagged_as_unconfirmed():
    """A modelled date is not a listed contract. Anything that later treats
    this as a real expiry must be able to tell the difference."""
    legs = OptionsLayer._date_theoretical_legs(_theoretical_legs(), dte=45,
                                               today=date(2026, 9, 8))
    assert all(l.get("expiration_estimated") is True for l in legs)


def test_real_chain_legs_are_never_overwritten():
    """A confirmed listed expiry outranks anything we would model."""
    legs = [{"action": "sell", "option_type": "put", "strike": 739.0,
             "expiration": "2026-10-16"}]
    out = OptionsLayer._date_theoretical_legs(legs, dte=45,
                                              today=date(2026, 9, 8))
    assert out[0]["expiration"] == "2026-10-16"
    assert not out[0].get("expiration_estimated")


# ── the label that drifted ───────────────────────────────────────

def test_expiration_line_reports_actual_days_not_requested_days():
    """Published briefs said '(45 days)' next to expiries that were 38 to 50
    days out. The date is the fact; the requested DTE is an intention."""
    legs = [{"expiration": "2026-10-16"}]
    line = OptionsLayer._expiration_line(legs, 45, today=date(2026, 9, 8))
    assert "2026-10-16" in line
    assert "38 days" in line          # the true distance
    assert "(45 days)" not in line    # the intention, wrongly stated as fact


def test_expiration_line_marks_an_estimated_date_as_a_target():
    legs = [{"expiration": "2026-10-23", "expiration_estimated": True}]
    line = OptionsLayer._expiration_line(legs, 45, today=date(2026, 9, 8))
    assert "target" in line.lower() or "est" in line.lower()
