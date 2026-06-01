# tests/test_intraday_structure_builder.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.intraday_structure_builder import (
    select_legs, structure_for_strategy,
    CONDOR_SHORT_OTM, CONDOR_WING, DEBIT_SHORT_OTM,
)


def test_iron_condor_geometry():
    legs = select_legs("iron_condor", spot=500.0)
    assert legs == [
        {"action": "SELL", "cp": "P", "strike": 497},
        {"action": "BUY",  "cp": "P", "strike": 492},
        {"action": "SELL", "cp": "C", "strike": 503},
        {"action": "BUY",  "cp": "C", "strike": 508},
    ]


def test_bull_debit_geometry():
    assert select_legs("bull_debit", spot=500.0) == [
        {"action": "BUY",  "cp": "C", "strike": 500},
        {"action": "SELL", "cp": "C", "strike": 503},
    ]


def test_bear_debit_geometry():
    assert select_legs("bear_debit", spot=500.0) == [
        {"action": "BUY",  "cp": "P", "strike": 500},
        {"action": "SELL", "cp": "P", "strike": 497},
    ]


def test_strike_rounding_to_dollar_grid():
    legs = select_legs("bull_debit", spot=500.4)
    assert legs[0]["strike"] == 500   # round(500.4)


def test_router_strategy_maps_to_structure():
    assert structure_for_strategy("call_debit_spread") == "bull_debit"
    assert structure_for_strategy("put_debit_spread")  == "bear_debit"
    assert structure_for_strategy("iron_condor")       == "iron_condor"


def test_unknown_structure_returns_empty():
    assert select_legs("nonsense", spot=500.0) == []


def test_select_legs_matches_legacy_build_0dte_legs():
    from backtests.intraday_backtest import build_0dte_legs
    for structure in ("iron_condor", "bull_debit", "bear_debit"):
        for spot in (487.3, 500.0, 612.49):
            assert select_legs(structure, spot) == build_0dte_legs(spot, structure), structure


def test_net_premium_credit_iron_condor():
    from signals.intraday_structure_builder import _net_premium
    # priced legs: shorts collect, longs pay. IC short mids 1.20+1.10, long 0.40+0.35
    priced = [
        {"action": "SELL", "mid": 1.20}, {"action": "BUY", "mid": 0.40},
        {"action": "SELL", "mid": 1.10}, {"action": "BUY", "mid": 0.35},
    ]
    # credit = (1.20+1.10) - (0.40+0.35) = 1.55
    assert round(_net_premium(priced, "iron_condor"), 2) == 1.55


def test_net_premium_debit_bull():
    from signals.intraday_structure_builder import _net_premium
    priced = [{"action": "BUY", "mid": 2.00}, {"action": "SELL", "mid": 0.80}]
    assert round(_net_premium(priced, "bull_debit"), 2) == 1.20  # 2.00 - 0.80


def test_risk_credit():
    from signals.intraday_structure_builder import _risk
    mp, ml = _risk("iron_condor", entry=1.55)
    assert mp == round(1.55 * 100, 2)                 # 155.0
    assert ml == round((CONDOR_WING - 1.55) * 100, 2) # (5-1.55)*100 = 345.0


def test_risk_debit():
    from signals.intraday_structure_builder import _risk
    mp, ml = _risk("bull_debit", entry=1.20)
    assert mp == round((DEBIT_SHORT_OTM - 1.20) * 100, 2)  # (3-1.2)*100 = 180.0
    assert ml == round(1.20 * 100, 2)                      # 120.0


# ---------------------------------------------------------------------------
# Task 4: LiveChainPricer
# ---------------------------------------------------------------------------

from datetime import date


class _FakeChain:
    """Stand-in for OptionsChain.get_chain returning canned contracts."""
    def __init__(self, contracts): self._c = contracts
    def get_chain(self, ticker, contract_type, min_expiration, max_expiration,
                  strike_min=None, strike_max=None, limit=50):
        min_iso = min_expiration.isoformat()
        max_iso = max_expiration.isoformat()
        return [
            c for c in self._c
            if c["type"] == contract_type
            and min_iso <= c["expiration"] <= max_iso
        ]


def _contract(strike, cp, mid, exp="2026-06-01"):
    return {"ticker": f"O:SPY..{cp}{strike}", "strike": float(strike),
            "expiration": exp, "dte": 0, "type": cp, "mid": mid,
            "bid": mid, "ask": mid, "delta": None}


def test_live_pricer_prices_iron_condor():
    from signals.intraday_structure_builder import LiveChainPricer
    chain = _FakeChain([
        _contract(497, "put", 1.20), _contract(492, "put", 0.40),
        _contract(503, "call", 1.10), _contract(508, "call", 0.35),
    ])
    legs = select_legs("iron_condor", spot=500.0)
    out = LiveChainPricer(chain).price(legs, "iron_condor", "0DTE", spot=500.0,
                                       as_of=date(2026, 6, 1))
    assert round(out["entry_price"], 2) == 1.55
    assert out["max_profit"] == 155.0
    assert out["max_loss"] == 345.0
    # journal leg shape
    assert all(set(("action", "type", "option_type", "strike", "expiration", "expiry", "mid")) <= set(l) for l in out["legs"])
    assert {l["type"] for l in out["legs"]} == {"put", "call"}


def test_live_pricer_returns_none_when_a_leg_missing():
    from signals.intraday_structure_builder import LiveChainPricer
    chain = _FakeChain([_contract(497, "put", 1.20)])  # only one of four legs
    legs = select_legs("iron_condor", spot=500.0)
    assert LiveChainPricer(chain).price(legs, "iron_condor", "0DTE", 500.0,
                                        as_of=date(2026, 6, 1)) is None


def test_live_pricer_1to3dte_picks_nearest_covering_expiry():
    """1-3DTE window: all four legs exist at as_of+2 AND some at as_of+3.
    Pricer must pick as_of+2 (nearest that covers ALL legs) for every journal leg."""
    from signals.intraday_structure_builder import LiveChainPricer
    as_of = date(2026, 6, 1)
    near = (as_of + __import__("datetime").timedelta(days=2)).isoformat()  # 2026-06-03
    far  = (as_of + __import__("datetime").timedelta(days=3)).isoformat()  # 2026-06-04
    # Full iron_condor set at near expiry
    contracts = [
        _contract(497, "put",  1.20, exp=near),
        _contract(492, "put",  0.40, exp=near),
        _contract(503, "call", 1.10, exp=near),
        _contract(508, "call", 0.35, exp=near),
        # Extra contracts at far expiry (should not be chosen)
        _contract(503, "call", 1.05, exp=far),
        _contract(508, "call", 0.30, exp=far),
    ]
    chain = _FakeChain(contracts)
    legs = select_legs("iron_condor", spot=500.0)
    out = LiveChainPricer(chain).price(legs, "iron_condor", "1-3DTE",
                                       spot=500.0, as_of=as_of)
    assert out is not None, "Expected a priced structure; got None"
    # ALL journal legs must show the nearest covering expiry
    for leg in out["legs"]:
        assert leg["expiration"] == near, (
            f"Expected expiration={near!r}, got {leg['expiration']!r}"
        )
        assert leg["expiry"] == near


def test_live_pricer_1to3dte_none_when_no_single_expiry_covers_all():
    """Put legs only at as_of+1, call legs only at as_of+3.
    No single expiry has ALL four iron_condor legs → must return None."""
    from signals.intraday_structure_builder import LiveChainPricer
    as_of = date(2026, 6, 1)
    puts_exp  = (as_of + __import__("datetime").timedelta(days=1)).isoformat()  # 2026-06-02
    calls_exp = (as_of + __import__("datetime").timedelta(days=3)).isoformat()  # 2026-06-04
    contracts = [
        _contract(497, "put",  1.20, exp=puts_exp),
        _contract(492, "put",  0.40, exp=puts_exp),
        _contract(503, "call", 1.10, exp=calls_exp),
        _contract(508, "call", 0.35, exp=calls_exp),
    ]
    chain = _FakeChain(contracts)
    legs = select_legs("iron_condor", spot=500.0)
    result = LiveChainPricer(chain).price(legs, "iron_condor", "1-3DTE",
                                          spot=500.0, as_of=as_of)
    assert result is None, f"Expected None (no covering expiry); got {result}"
