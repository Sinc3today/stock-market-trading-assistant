"""tests/test_mark_spread.py -- the mark every forward test depends on.

_mark_spread decides what an open paper position is worth, which decides
whether it closes and at what P&L. Since the Phase 1 consolidation it is the
single marking function behind ALL four generators, so a defect here is no
longer contained to one rung.

It matched leg direction with `leg.get("action") == "BUY"` — case-sensitive.
The journal holds both casings: condor_calc writes "BUY"/"SELL", options_layer
writes "buy"/"sell". For a lowercase position every leg fell to the else
branch and was priced as a SHORT, giving a $1,490 error on a 1-lot condor
close. One open disciplined position carried lowercase legs.

Two silent neighbours in the same three lines: an unrecognised action was
treated as a short rather than refused, and a leg with no type at all was
priced as a CALL.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.dipbuy_forward import _mark_spread


def _condor(case=str.upper):
    return [{"action": case("sell"), "option_type": "call", "strike": 805},
            {"action": case("buy"), "option_type": "call", "strike": 810},
            {"action": case("sell"), "option_type": "put", "strike": 738},
            {"action": case("buy"), "option_type": "put", "strike": 733}]


def test_leg_casing_does_not_change_the_mark():
    """The defect: 22 buy + 22 sell legs in the journal are lowercase."""
    up = _mark_spread(_condor(str.upper), 770.0, 15.0, 45)
    lo = _mark_spread(_condor(str.lower), 770.0, 15.0, 45)
    assert up == pytest.approx(lo), "leg casing changed the valuation"


def test_mixed_casing_is_handled():
    legs = _condor(str.upper)
    legs[0]["action"] = "sell"
    legs[2]["action"] = "Sell"
    assert _mark_spread(legs, 770.0, 15.0, 45) == pytest.approx(
        _mark_spread(_condor(str.upper), 770.0, 15.0, 45))


def test_a_short_condor_marks_as_a_liability():
    """Sold premium: net value is negative, cost to close is positive."""
    assert _mark_spread(_condor(), 770.0, 15.0, 45) < 0


def test_a_long_debit_spread_marks_positive():
    legs = [{"action": "BUY", "option_type": "call", "strike": 760},
            {"action": "SELL", "option_type": "call", "strike": 765}]
    assert _mark_spread(legs, 770.0, 15.0, 45) > 0


def test_an_unrecognised_action_is_refused_not_treated_as_short():
    """Silently pricing an unknown leg as a short is how a wrong mark becomes
    a recorded exit."""
    legs = _condor()
    legs[0]["action"] = "OPEN"
    with pytest.raises(ValueError, match="(?i)action"):
        _mark_spread(legs, 770.0, 15.0, 45)


def test_a_leg_with_no_option_type_is_refused_not_assumed_to_be_a_call():
    legs = [{"action": "BUY", "strike": 760}]
    with pytest.raises(ValueError, match="(?i)option type"):
        _mark_spread(legs, 770.0, 15.0, 45)


def test_a_leg_with_no_strike_is_refused():
    legs = [{"action": "BUY", "option_type": "call"}]
    with pytest.raises((ValueError, KeyError)):
        _mark_spread(legs, 770.0, 15.0, 45)


def test_put_and_call_are_not_interchangeable():
    call = [{"action": "BUY", "option_type": "call", "strike": 760}]
    put = [{"action": "BUY", "option_type": "put", "strike": 760}]
    assert _mark_spread(call, 770.0, 15.0, 45) != pytest.approx(
        _mark_spread(put, 770.0, 15.0, 45))


def test_the_type_alias_is_honoured():
    a = [{"action": "BUY", "type": "put", "strike": 760}]
    b = [{"action": "BUY", "option_type": "put", "strike": 760}]
    assert _mark_spread(a, 770.0, 15.0, 45) == pytest.approx(
        _mark_spread(b, 770.0, 15.0, 45))
