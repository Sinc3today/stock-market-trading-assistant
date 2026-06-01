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
