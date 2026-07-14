"""tests/test_regime_view.py -- pure builders for the /regime page."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_condor_payoff_shape():
    # flat max-profit between shorts, max loss beyond the wings
    from alerts.regime_view import payoff_points
    legs = [("call", 770, -1), ("call", 775, +1), ("put", 730, -1), ("put", 725, +1)]
    credit = 1.5
    pts = dict(payoff_points(legs, -credit, 700, 800, n=100))
    mid = pts[min(pts, key=lambda s: abs(s - 750))]
    assert mid == pytest.approx(credit * 100)                 # inside the range
    far_up = pts[max(pts)]
    assert far_up == pytest.approx((credit - 5) * 100)        # beyond call wing
    far_dn = pts[min(pts)]
    assert far_dn == pytest.approx((credit - 5) * 100)        # beyond put wing


def test_debit_spread_payoff_shape():
    from alerts.regime_view import payoff_points
    legs = [("call", 750, +1), ("call", 765, -1)]
    debit = 6.0
    pts = dict(payoff_points(legs, debit, 700, 800, n=100))
    assert pts[min(pts)] == pytest.approx(-debit * 100)       # below long: lose debit
    assert pts[max(pts)] == pytest.approx((15 - 6) * 100)     # above short: width-debit


def test_pop_sanity():
    from alerts.regime_view import pop_above, pop_between
    # ATM breakeven ~ coin flip; deep-ITM breakeven ~ certain
    assert 0.35 < pop_above(750, 16, 45, 750) < 0.60
    assert pop_above(750, 16, 45, 600) > 0.95
    p = pop_between(750, 16, 45, 720, 780)
    assert 0.3 < p < 0.95
    # wider range => higher probability
    assert pop_between(750, 16, 45, 700, 800) > p


def test_build_structures_statuses_choppy_vs_trending():
    from alerts.regime_view import build_structures
    chop = {d["key"]: d["status"] for d in build_structures(750, 15, "choppy_low_vol")}
    assert chop["iron_condor"] == "validated" and chop["butterfly"] == "validated"
    assert chop["put_credit"] == "off-regime" and chop["call_debit"] == "off-regime"
    trend = {d["key"]: d["status"] for d in build_structures(750, 15, "trending_up_calm")}
    assert trend["put_credit"] == "validated" and trend["call_debit"] == "validated"
    assert trend["iron_condor"] == "off-regime"
    hv = {d["key"]: d["status"] for d in build_structures(750, 25, "trending_high_vol")}
    assert set(hv.values()) == {"stand-down"}


def test_build_structures_carry_pop_and_history():
    from alerts.regime_view import build_structures
    for d in build_structures(750, 15, "choppy_low_vol"):
        assert 0.0 < d["pop"] < 1.0
        assert d["hist"][0].endswith("%") and d["hist"][1]
        assert d["max_profit"] > 0 and d["max_loss"] > 0


def test_svg_builders_emit_valid_markup():
    from alerts.regime_view import gauge_svg, payoff_svg
    g = gauge_svg("ADX — trend strength", 33.0, 0, 50, [(32, "trending ≥32")])
    assert g.startswith("<svg") and "33.0" in g and "trending" in g
    p = payoff_svg([("call", 750, +1), ("call", 765, -1)], 6.0, spot=750.0)
    assert p.startswith("<svg") and "polyline" in p and "SPY 750" in p
