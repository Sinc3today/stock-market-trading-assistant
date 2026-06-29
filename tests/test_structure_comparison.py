"""tests/test_structure_comparison.py -- payoff math for the structure study.

Guards the expiry-payoff and structure builders the capital comparison rests on.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.structure_comparison import (
    condor_legs, butterfly_legs, _value_at_expiry, _wing_width,
)


def test_condor_legs_shape_and_width():
    legs = condor_legs(700.0, 0.02)
    assert len(legs) == 4
    # shorts 2.5% OTM, wings 2% beyond -> ~$14 wide on a $700 spot
    assert round(_wing_width(legs), 1) == 14.0


def test_condor_expiry_payoff_in_range_is_zero():
    # condor between the shorts -> all legs expire worthless -> position value 0
    legs = condor_legs(700.0, 0.02)
    assert _value_at_expiry(legs, 700.0) == 0.0
    # breach above the short call -> the position is a liability (negative value)
    assert _value_at_expiry(legs, 730.0) < 0


def test_butterfly_peaks_at_center():
    legs = butterfly_legs(700.0)        # [682.5, 700, 714] call fly
    at_center = _value_at_expiry(legs, 700.0)
    at_edge = _value_at_expiry(legs, 717.5)
    assert at_center > at_edge          # tent peaks at the body
    assert at_center > 0
    # max value at center ~ half-width (17.5 here)
    assert round(at_center, 1) == 17.5
