"""tests/test_structure_single_expiry.py -- ENUMERATION: every multi-leg
structure builder must return legs that share one expiration.

The 2026-09-07 audit's core lesson was that fixing one instance of a defect
class is how "I fixed 3 of 13" happens. find_iron_condor was returning legs
across three expirations; find_vertical_spread has the identical shape --
get_chain returns a date RANGE, and _closest_strike picks from all of it, so
a "vertical" could be a diagonal with a fabricated max loss.

This file enumerates the builders rather than testing one, so a NEW builder
with the same shape fails here instead of shipping.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.options_chain import OptionsChain

LEG_KEYS = ("short_call", "long_call", "short_put", "long_put",
            "buy_leg", "sell_leg")


def _c(strike, delta, mark, otype="call", exp="2026-10-23", dte=45):
    return {"strike": float(strike), "delta": delta, "mark": mark, "mid": None,
            "type": otype, "expiration": exp, "dte": dte,
            "ticker": f"O:X{exp}{otype[0]}{int(strike)}", "open_interest": 100}


class _Chain(OptionsChain):
    def __init__(self, calls, puts):
        self._calls, self._puts = calls, puts

    def get_chain(self, ticker, otype, min_exp, max_exp, **kw):
        return self._calls if otype == "call" else self._puts


def _two_expiry(otype, strikes_deltas):
    """Same strikes listed under two expirations, with different marks."""
    out = []
    for exp, dte, bump in (("2026-10-23", 45, 0.0), ("2026-10-30", 52, 2.5)):
        for k, d, m in strikes_deltas:
            out.append(_c(k, d, m + bump, otype, exp, dte))
    return out


# Near-the-money strikes for the verticals, far-OTM strikes for the condor
# wings — one fixture has to serve both builders.
CALLS = _two_expiry("call", [(769, 0.50, 16.20), (774, 0.42, 13.40),
                             (764, 0.58, 19.60),
                             (797, 0.16, 3.10), (802, 0.12, 2.10),
                             (803, 0.15, 2.68), (808, 0.11, 1.95)])
PUTS = _two_expiry("put", [(769, -0.50, 15.80), (764, -0.42, 13.10),
                           (774, -0.58, 19.10),
                           (735, -0.16, 4.02), (730, -0.13, 3.40),
                           (727, -0.15, 3.90), (722, -0.12, 3.30)])


def _legs_of(struct):
    return [struct[k] for k in LEG_KEYS if isinstance(struct.get(k), dict)]


def test_the_builders_are_all_covered_here():
    """If someone adds a find_* structure builder, this list must grow."""
    builders = {n for n, _ in inspect.getmembers(OptionsChain, inspect.isfunction)
                if n.startswith("find_")}
    assert builders == {"find_iron_condor", "find_vertical_spread"}, (
        f"new structure builder(s) {builders} — add a single-expiry case here")


def test_iron_condor_legs_share_one_expiration():
    ic = _Chain(CALLS, PUTS).find_iron_condor(ticker="SPY", spot=769.0,
                                              dte_target=45)
    assert ic is not None
    assert len({l["expiration"] for l in _legs_of(ic)}) == 1


@pytest.mark.parametrize("kind,direction", [("debit", "bullish"),
                                            ("credit", "bearish")])
def test_vertical_spread_legs_share_one_expiration(kind, direction):
    """A vertical whose legs expire on different dates is a diagonal, and its
    max loss -- computed from the strike distance alone -- is fiction."""
    sp = _Chain(CALLS, PUTS).find_vertical_spread(
        ticker="SPY", direction=direction, kind=kind, spot=769.0, dte_target=45)
    assert sp is not None
    assert len({l["expiration"] for l in _legs_of(sp)}) == 1


@pytest.mark.parametrize("kind,direction", [("debit", "bullish"),
                                            ("credit", "bearish")])
def test_vertical_reports_the_expiration_its_legs_actually_use(kind, direction):
    sp = _Chain(CALLS, PUTS).find_vertical_spread(
        ticker="SPY", direction=direction, kind=kind, spot=769.0, dte_target=45)
    for leg in _legs_of(sp):
        assert leg["expiration"] == sp["expiration"]
