"""
tests/test_portfolio_greeks.py -- aggregate Greeks across open trades.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.portfolio_greeks import PortfolioGreeks, CONTRACT_MULTIPLIER


# ─────────────────────────────────────────
# Stubs
# ─────────────────────────────────────────

class StubTrades:
    """Replaces TradeRecorder; controls what get_open_trades returns."""
    def __init__(self, open_trades):
        self._open = open_trades
    def get_open_trades(self):
        return self._open


def _leg(action, *, type_, strike, ticker=None, exp="2026-06-15",
         delta=None, theta=None, vega=None, gamma=None):
    leg = {
        "action":     action,
        "type":       type_,
        "strike":     strike,
        "expiration": exp,
    }
    if ticker is not None: leg["ticker"] = ticker
    if delta  is not None: leg["delta"]  = delta
    if theta  is not None: leg["theta"]  = theta
    if vega   is not None: leg["vega"]   = vega
    if gamma  is not None: leg["gamma"]  = gamma
    return leg


def _trade(trade_id, strategy, legs, size=1, ticker="SPY"):
    return {
        "trade_id": trade_id,
        "ticker":   ticker,
        "strategy": strategy,
        "size":     size,
        "outcome":  "open",
        "legs":     legs,
    }


# ─────────────────────────────────────────
# Empty + degraded paths
# ─────────────────────────────────────────

def test_empty_portfolio_is_zero():
    out = PortfolioGreeks(trade_recorder=StubTrades([])).compute()
    assert out["open_trade_count"] == 0
    assert out["positions"]        == []
    assert out["total"]            == {"delta": 0.0, "gamma": 0.0,
                                        "theta": 0.0, "vega": 0.0}


def test_legacy_trade_skipped_with_warning():
    """A trade whose legs have no Greeks AND no ticker is un-priceable."""
    legs = [
        _leg("buy",  type_="call", strike=700.0),  # no Greeks, no ticker
        _leg("sell", type_="call", strike=710.0),
    ]
    out = PortfolioGreeks(
        trade_recorder=StubTrades([_trade("T1", "debit_spread", legs)]),
    ).compute()
    assert out["skipped_legs"]      == 2
    assert out["positions"][0]["warning"]
    assert out["positions"][0]["delta"] == 0.0


# ─────────────────────────────────────────
# Sign convention + size multiplier
# ─────────────────────────────────────────

def test_long_call_is_positive_delta():
    legs = [_leg("buy", type_="call", strike=700.0, ticker="O:A",
                  delta=0.55, theta=-0.04, vega=0.18, gamma=0.01)]
    out = PortfolioGreeks(
        trade_recorder=StubTrades([_trade("T1", "single_leg", legs, size=2)]),
    ).compute()
    # delta = 0.55 × 2 contracts × 100 multiplier = +110
    assert out["total"]["delta"] == pytest.approx(110.0)
    assert out["total"]["theta"] == pytest.approx(-0.04 * 2 * 100)
    assert out["positions"][0]["contracts"] == 2


def test_short_call_inverts_signs():
    legs = [_leg("sell", type_="call", strike=720.0, ticker="O:B",
                  delta=0.45, theta=-0.05, vega=0.20, gamma=0.012)]
    out = PortfolioGreeks(
        trade_recorder=StubTrades([_trade("T1", "single_leg", legs)]),
    ).compute()
    # Short call = -delta, -vega, +theta (you COLLECT theta when short)
    # Sign convention here is: short flips all Greeks. theta on a leg is
    # already negative for a long option; flipping makes it positive,
    # which represents the time-decay income on the short side.
    assert out["total"]["delta"] == pytest.approx(-45.0)
    assert out["total"]["theta"] == pytest.approx(+5.0)   # -(-0.05) * 100
    assert out["total"]["vega"]  == pytest.approx(-20.0)


def test_iron_condor_net_delta_near_zero():
    """A balanced iron condor at +/- 0.20 delta short legs should have
    near-zero net delta after summing both sides."""
    legs = [
        _leg("sell", type_="call", strike=750, ticker="O:SC", delta=+0.20, theta=-0.03, vega=0.10),
        _leg("buy",  type_="call", strike=755, ticker="O:LC", delta=+0.10, theta=-0.02, vega=0.07),
        _leg("sell", type_="put",  strike=710, ticker="O:SP", delta=-0.20, theta=-0.03, vega=0.10),
        _leg("buy",  type_="put",  strike=705, ticker="O:LP", delta=-0.10, theta=-0.02, vega=0.07),
    ]
    out = PortfolioGreeks(
        trade_recorder=StubTrades([_trade("T1", "iron_condor", legs)]),
    ).compute()
    # Short call: -0.20, Long call: +0.10, Short put: +0.20, Long put: -0.10
    # Sum: 0.0 × 100 = 0.0
    assert out["total"]["delta"] == pytest.approx(0.0, abs=0.5)
    # Theta net positive: collect more from shorts (-(-0.03)×2) than pay on longs (-0.02×2 with sign +)
    assert out["total"]["theta"] > 0


# ─────────────────────────────────────────
# Multiple positions
# ─────────────────────────────────────────

def test_multiple_open_positions_sum():
    t1 = _trade("T1", "single_leg",
                [_leg("buy", type_="call", strike=700, ticker="O:A",
                       delta=0.5, theta=-0.03, vega=0.15)])
    t2 = _trade("T2", "single_leg",
                [_leg("buy", type_="put",  strike=695, ticker="O:B",
                       delta=-0.3, theta=-0.04, vega=0.18)])
    out = PortfolioGreeks(trade_recorder=StubTrades([t1, t2])).compute()
    assert out["open_trade_count"] == 2
    assert len(out["positions"])    == 2
    # delta: (+0.5 + -0.3) × 100 = +20
    assert out["total"]["delta"] == pytest.approx(20.0)


def test_partial_pricing_marks_position_warning():
    """One leg has Greeks, one doesn't — totals partial, warning surfaced."""
    legs = [
        _leg("buy",  type_="call", strike=700, ticker="O:A",
              delta=0.5, theta=-0.03, vega=0.15),
        _leg("sell", type_="call", strike=710),                # un-priceable
    ]
    out = PortfolioGreeks(
        trade_recorder=StubTrades([_trade("T1", "debit_spread", legs)]),
    ).compute()
    assert out["skipped_legs"] == 1
    pos = out["positions"][0]
    assert pos["warning"] and "1 of 2 legs un-priced" in pos["warning"]
    # The priced leg's delta still counts toward the partial total
    assert out["total"]["delta"] == pytest.approx(50.0)


def test_data_failure_returns_empty():
    """If TradeRecorder raises, we still return a valid empty dict."""
    class Broken:
        def get_open_trades(self):
            raise RuntimeError("disk gone")
    out = PortfolioGreeks(trade_recorder=Broken()).compute()
    assert out["open_trade_count"] == 0
    assert out["positions"]        == []
