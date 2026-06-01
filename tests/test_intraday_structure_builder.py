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
