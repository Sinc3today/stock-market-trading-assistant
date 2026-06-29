"""tests/test_condor_calc.py -- on-demand condor calculator for the copilot.

Builds a condor at the CURRENT SPY price (0.20-delta shorts + $5 wings, matching
the user's real trade), priced at current VIX, so they can mirror it on RH even
if they missed the morning notification.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_build_condor_shapes_a_valid_condor():
    from signals.condor_calc import build_condor
    c = build_condor(spot=745.0, vix=18.0, dte=45, wing=5.0)
    # shorts straddle the price; longs are the wings $5 beyond
    assert c["short_put"] < 745.0 < c["short_call"]
    assert round(c["long_call"] - c["short_call"], 1) == 5.0
    assert round(c["short_put"] - c["long_put"], 1) == 5.0
    # it's a credit, capped by the wing width
    assert c["credit"] > 0
    assert round(c["max_loss"] + c["max_profit"], 0) == 500.0   # ($5 - credit + credit) * 100
    # breakevens = shorts +/- the credit (per share)
    assert round(c["breakeven_low"], 2) == round(c["short_put"] - c["credit"], 2)
    assert round(c["breakeven_high"], 2) == round(c["short_call"] + c["credit"], 2)
    # four legs, ordered/usable
    assert len(c["legs"]) == 4


def test_shorts_are_near_target_delta():
    from signals.condor_calc import build_condor, _delta
    c = build_condor(spot=745.0, vix=18.0, dte=45, short_delta=0.20, wing=5.0)
    t = 45 / 365.0
    call_d = _delta("call", 745.0, c["short_call"], t, 0.18)
    put_d = abs(_delta("put", 745.0, c["short_put"], t, 0.18))
    assert 0.13 < call_d < 0.27
    assert 0.13 < put_d < 0.27


def test_higher_vix_widens_the_condor():
    from signals.condor_calc import build_condor
    calm = build_condor(spot=745.0, vix=13.0)
    wild = build_condor(spot=745.0, vix=30.0)
    # higher vol -> 0.20-delta strikes sit further from spot
    assert wild["short_call"] > calm["short_call"]
    assert wild["short_put"] < calm["short_put"]
