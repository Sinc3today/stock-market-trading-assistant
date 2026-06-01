"""
tests/test_options_chain.py -- OptionsChain with stubbed Polygon SDK.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.options_chain import OptionsChain


# ─────────────────────────────────────────
# FAKE SDK PIECES
# ─────────────────────────────────────────

def _fake_snapshot(
    *, ticker, strike, expiration, contract_type,
    delta=0.50, gamma=0.01, theta=-0.05, vega=0.1,
    iv=0.20, bid=1.00, ask=1.10, oi=1000, volume=500,
    day_close=None, vwap=None,
):
    return SimpleNamespace(
        details            = SimpleNamespace(
            ticker=ticker, strike_price=strike,
            expiration_date=expiration, contract_type=contract_type,
        ),
        greeks             = SimpleNamespace(
            delta=delta, gamma=gamma, theta=theta, vega=vega),
        implied_volatility = iv,
        last_quote         = SimpleNamespace(bid=bid, ask=ask),
        day                = SimpleNamespace(volume=volume, vwap=vwap, close=day_close),
        open_interest      = oi,
    )


def _build_call_chain(spot: float, exp_days: int):
    """Standard call chain at strikes 5 below/above spot."""
    exp_date = (date.today() + timedelta(days=exp_days)).isoformat()
    out = []
    for k_offset, d in [(-10, 0.85), (-5, 0.70), (0, 0.50), (5, 0.30),
                         (10, 0.20), (15, 0.12), (20, 0.06)]:
        k = round(spot + k_offset, 2)
        out.append(_fake_snapshot(
            ticker=f"O:SPY{exp_date.replace('-', '')}C{int(k*1000):08d}",
            strike=k, expiration=exp_date, contract_type="call",
            delta=d, bid=max(0.05, 1.0 - k_offset*0.05), ask=max(0.10, 1.05 - k_offset*0.05),
        ))
    return out


def _build_put_chain(spot: float, exp_days: int):
    exp_date = (date.today() + timedelta(days=exp_days)).isoformat()
    out = []
    for k_offset, d in [(-20, -0.06), (-15, -0.12), (-10, -0.20),
                         (-5, -0.30), (0, -0.50), (5, -0.70), (10, -0.85)]:
        k = round(spot + k_offset, 2)
        out.append(_fake_snapshot(
            ticker=f"O:SPY{exp_date.replace('-', '')}P{int(k*1000):08d}",
            strike=k, expiration=exp_date, contract_type="put",
            delta=d, bid=max(0.05, 1.0 + k_offset*0.05), ask=max(0.10, 1.05 + k_offset*0.05),
        ))
    return out


class FakePolygonSDK:
    """Stand-in for polygon.RESTClient."""
    def __init__(self, chains_by_type: dict):
        self._chains = chains_by_type   # {"call": [...], "put": [...]}
        self.calls = 0
    def list_snapshot_options_chain(self, underlying_asset=None, params=None):
        self.calls += 1
        return iter(self._chains.get(params["contract_type"], []))


@pytest.fixture
def oc_with(monkeypatch):
    """Build an OptionsChain whose RESTClient is replaced by FakePolygonSDK."""
    def factory(call_chain=None, put_chain=None):
        oc = OptionsChain()
        oc._client = FakePolygonSDK({
            "call": call_chain or [],
            "put":  put_chain or [],
        })
        return oc
    return factory


# ─────────────────────────────────────────
# get_chain + normalisation
# ─────────────────────────────────────────

def test_get_chain_returns_normalised_dicts(oc_with):
    chain = _build_call_chain(spot=600.0, exp_days=14)
    oc    = oc_with(call_chain=chain)
    out   = oc.get_chain("SPY", "call",
                          date.today(), date.today() + timedelta(days=20))
    assert len(out) == len(chain)
    first = out[0]
    # Shape check
    assert {"ticker", "strike", "expiration", "dte", "type", "mid",
            "bid", "ask", "iv", "delta", "gamma", "theta", "vega",
            "open_interest", "volume"} <= set(first.keys())
    assert first["type"] == "call"


def test_get_chain_caches_within_ttl(oc_with):
    chain = _build_call_chain(spot=600.0, exp_days=14)
    oc    = oc_with(call_chain=chain)
    oc.get_chain("SPY", "call", date.today(), date.today() + timedelta(days=20))
    calls_after_first = oc._client.calls
    oc.get_chain("SPY", "call", date.today(), date.today() + timedelta(days=20))
    assert oc._client.calls == calls_after_first   # cache hit


def test_get_chain_returns_empty_on_api_error(oc_with):
    oc = oc_with()  # FakePolygonSDK returns [] for missing contract type
    out = oc.get_chain("SPY", "call",
                        date.today(), date.today() + timedelta(days=20))
    assert out == []


def test_normalise_handles_missing_quote_bid_ask():
    """Saturday eve / pre-market: bid/ask is None -> mid=None, not 0."""
    bad = SimpleNamespace(
        details            = SimpleNamespace(
            ticker="O:X", strike_price=600.0,
            expiration_date=(date.today() + timedelta(days=14)).isoformat(),
            contract_type="call",
        ),
        greeks             = SimpleNamespace(delta=0.5, gamma=0.01, theta=-0.05, vega=0.1),
        implied_volatility = 0.2,
        last_quote         = SimpleNamespace(bid=None, ask=None),
        day                = SimpleNamespace(volume=None, vwap=None, close=None),
        open_interest      = 0,
    )
    out = OptionsChain._normalise(bad)
    assert out["mid"] is None
    assert out["mark"] is None   # no quote AND no day price → no mark
    assert out["delta"] == 0.5


def test_normalise_mark_falls_back_to_day_close_when_no_quote():
    """This Polygon plan's snapshot has no bid/ask (mid=None); mark must fall
    back to day.close so LiveChainPricer can still price. mid stays None."""
    snap = _fake_snapshot(
        ticker="O:X", strike=762.0,
        expiration=(date.today()).isoformat(), contract_type="call",
        bid=None, ask=None, day_close=0.37, vwap=0.12,
    )
    out = OptionsChain._normalise(snap)
    assert out["mid"] is None
    assert out["mark"] == 0.37


def test_normalise_mark_prefers_quote_midpoint_over_day_close():
    """When a real quote exists, mark is the quote midpoint, not day.close."""
    snap = _fake_snapshot(
        ticker="O:X", strike=600.0,
        expiration=(date.today()).isoformat(), contract_type="call",
        bid=1.00, ask=1.10, day_close=0.50,
    )
    out = OptionsChain._normalise(snap)
    assert out["mid"] == 1.05
    assert out["mark"] == 1.05


# ─────────────────────────────────────────
# find_iron_condor
# ─────────────────────────────────────────

def test_find_iron_condor_picks_target_delta(oc_with):
    oc = oc_with(
        call_chain = _build_call_chain(spot=600.0, exp_days=14),
        put_chain  = _build_put_chain(spot=600.0, exp_days=14),
    )
    ic = oc.find_iron_condor("SPY", spot=600.0, dte_target=14,
                              short_delta=0.20, wing_width=5.0)
    assert ic is not None
    # Short legs sit at ~0.20 delta on each side
    assert abs(ic["short_call"]["delta"] - 0.20)        < 0.05
    assert abs(abs(ic["short_put"]["delta"]) - 0.20)    < 0.05
    # Wings are wider than shorts
    assert ic["long_call"]["strike"] > ic["short_call"]["strike"]
    assert ic["long_put"]["strike"]  < ic["short_put"]["strike"]
    # Expiration in expected window
    assert 7 <= ic["dte"] <= 21


def test_find_iron_condor_returns_none_when_chain_empty(oc_with):
    oc = oc_with()
    assert oc.find_iron_condor("SPY", spot=600.0, dte_target=14) is None


# ─────────────────────────────────────────
# find_vertical_spread
# ─────────────────────────────────────────

def test_find_vertical_bull_call_debit(oc_with):
    chain = _build_call_chain(spot=600.0, exp_days=21)
    oc    = oc_with(call_chain=chain)
    sp = oc.find_vertical_spread("SPY", direction="bullish", kind="debit",
                                  spot=600.0, dte_target=21, width=10.0)
    assert sp is not None
    # Bull call: buy ATM, sell further OTM call
    assert sp["sell_leg"]["strike"] > sp["buy_leg"]["strike"]
    assert sp["buy_leg"]["type"]   == "call"
    assert sp["sell_leg"]["type"]  == "call"


def test_find_vertical_bull_put_credit(oc_with):
    chain = _build_put_chain(spot=600.0, exp_days=21)
    oc    = oc_with(put_chain=chain)
    sp = oc.find_vertical_spread("SPY", direction="bullish", kind="credit",
                                  spot=600.0, dte_target=21, width=10.0)
    assert sp is not None
    # Bull put credit: sell ATM put, buy further OTM put (lower strike)
    assert sp["sell_leg"]["strike"] > sp["buy_leg"]["strike"]
    assert sp["buy_leg"]["type"]   == "put"


def test_find_vertical_bear_call_credit(oc_with):
    chain = _build_call_chain(spot=600.0, exp_days=21)
    oc    = oc_with(call_chain=chain)
    sp = oc.find_vertical_spread("SPY", direction="bearish", kind="credit",
                                  spot=600.0, dte_target=21, width=10.0)
    assert sp is not None
    # Bear call: sell ATM call, buy further OTM call (higher strike)
    assert sp["sell_leg"]["strike"] < sp["buy_leg"]["strike"]


def test_find_vertical_returns_none_when_strikes_overlap(oc_with):
    """If buy == sell (only one strike available), refuse rather than
    return a degenerate spread."""
    single = _build_call_chain(spot=600.0, exp_days=21)[:1]
    oc = oc_with(call_chain=single)
    sp = oc.find_vertical_spread("SPY", direction="bullish", kind="debit",
                                  spot=600.0, dte_target=21, width=10.0)
    assert sp is None
