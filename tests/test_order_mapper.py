"""tests/test_order_mapper.py -- pure OCC-symbol + Tradier multileg mapping.

The correctness-critical, network-free core of the Tradier integration
(docs/TRADIER_MIGRATION.md). Our build_condor/build_broken_wing legs must map
1:1 to Tradier's class=multileg params with correct OCC symbols and sides.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from brokers.order_mapper import occ_symbol, leg_side, build_multileg_order


def test_occ_symbol_call_and_put():
    assert occ_symbol("SPY", "2026-07-31", "CALL", 639.0) == "SPY260731C00639000"
    assert occ_symbol("SPY", "2026-07-31", "PUT", 612.0) == "SPY260731P00612000"


def test_occ_symbol_fractional_and_padding():
    # strike*1000 zero-padded to 8 digits; handles sub-$100 and fractional
    assert occ_symbol("QQQ", "2026-08-21", "call", 92.5) == "QQQ260821C00092500"
    assert occ_symbol("SPY", "2026-08-21", "P", 5.0) == "SPY260821P00005000"


def test_leg_side_open_close():
    assert leg_side("BUY", "open") == "buy_to_open"
    assert leg_side("SELL", "open") == "sell_to_open"
    assert leg_side("BUY", "close") == "buy_to_close"
    assert leg_side("SELL", "close") == "sell_to_close"


def _condor_legs():
    e = "2026-07-31"
    return [
        {"action": "BUY",  "option_type": "CALL", "strike": 644.0, "expiry": e},
        {"action": "SELL", "option_type": "CALL", "strike": 639.0, "expiry": e},
        {"action": "BUY",  "option_type": "PUT",  "strike": 612.0, "expiry": e},
        {"action": "SELL", "option_type": "PUT",  "strike": 617.0, "expiry": e},
    ]


def test_build_multileg_condor():
    params = build_multileg_order("SPY", _condor_legs(), quantity=2,
                                  order_type="credit", price=1.52)
    assert params["class"] == "multileg"
    assert params["symbol"] == "SPY"
    assert params["type"] == "credit"
    assert params["duration"] == "day"
    assert params["price"] == "1.52"
    # 4 legs, indexed 0..3, each with symbol/side/quantity
    assert params["option_symbol[0]"] == "SPY260731C00644000"
    assert params["side[0]"] == "buy_to_open"
    assert params["side[1]"] == "sell_to_open"
    assert params["quantity[0]"] == "2" and params["quantity[3]"] == "2"
    # exactly 4 legs present, none beyond
    assert "option_symbol[4]" not in params


def test_build_multileg_close_intent_flips_sides():
    params = build_multileg_order("SPY", _condor_legs(), quantity=1,
                                  order_type="debit", price=0.30, intent="close")
    assert params["side[0]"] == "sell_to_close"   # was buy_to_open
    assert params["side[1]"] == "buy_to_close"    # was sell_to_open


def test_broken_wing_duplicate_body_maps_to_two_legs():
    # BWB body is 2 short puts at the same strike -> two separate legs, both
    # sell_to_open, so Tradier sees the -2 quantity correctly.
    e = "2026-09-18"
    legs = [
        {"action": "BUY",  "option_type": "PUT", "strike": 621.0, "expiry": e},
        {"action": "SELL", "option_type": "PUT", "strike": 618.0, "expiry": e},
        {"action": "SELL", "option_type": "PUT", "strike": 618.0, "expiry": e},
        {"action": "BUY",  "option_type": "PUT", "strike": 610.0, "expiry": e},
    ]
    params = build_multileg_order("SPY", legs, quantity=1, order_type="credit", price=0.70)
    assert params["option_symbol[1]"] == params["option_symbol[2]"] == "SPY260918P00618000"
    assert params["side[1]"] == params["side[2]"] == "sell_to_open"


def test_reject_naked_leg():
    # A lone short with no long wing = undefined risk -> must be rejected
    # (defined-risk-only guardrail).
    with pytest.raises(ValueError):
        build_multileg_order("SPY", [
            {"action": "SELL", "option_type": "CALL", "strike": 640.0, "expiry": "2026-07-31"}],
            quantity=1, order_type="credit", price=1.0)
