"""tests/test_copilot_log_form.py -- manual copilot log form (pure helpers).

The form posts strikes by slot (buy-call/sell-call/buy-put/sell-put); these
helpers turn that into TradeRecorder.log_entry kwargs (with strategy/direction
inferred from the legs) and pre-fill the form from a vision-extracted play.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_build_kwargs_iron_condor():
    from alerts.copilot_log import build_live_trade_kwargs
    form = {"ticker": "spy", "expiry": "2026-07-17", "entry_price": "1.10",
            "max_profit": "110", "max_loss": "-390",
            "bc": "781", "sc": "776", "bp": "695", "sp": "700"}
    kw = build_live_trade_kwargs(form)
    assert kw["ticker"] == "SPY"
    assert kw["strategy"] == "iron_condor"
    assert kw["direction"] == "neutral"
    assert kw["entry_price"] == 1.10
    assert kw["book"] == "live"
    # legs ordered buy call, sell call, buy put, sell put
    assert [(l["action"], l["option_type"], l["strike"]) for l in kw["legs"]] == [
        ("BUY", "CALL", 781.0), ("SELL", "CALL", 776.0),
        ("BUY", "PUT", 695.0), ("SELL", "PUT", 700.0)]
    assert all(l["expiry"] == "2026-07-17" for l in kw["legs"])


def test_build_kwargs_debit_call_spread_is_bullish():
    from alerts.copilot_log import build_live_trade_kwargs
    # buy lower call, sell higher call -> debit, bullish
    kw = build_live_trade_kwargs({"bc": "700", "sc": "705", "entry_price": "2.0"})
    assert kw["strategy"] == "debit_spread"
    assert kw["direction"] == "bullish"
    assert len(kw["legs"]) == 2


def test_build_kwargs_single_leg_long_put_is_bearish():
    from alerts.copilot_log import build_live_trade_kwargs
    kw = build_live_trade_kwargs({"bp": "690", "entry_price": "3.0"})
    assert kw["strategy"] == "single_leg"
    assert kw["direction"] == "bearish"


def test_build_kwargs_no_legs_raises():
    from alerts.copilot_log import build_live_trade_kwargs
    with pytest.raises(ValueError):
        build_live_trade_kwargs({"ticker": "SPY", "entry_price": "1.0"})


def test_prefill_from_extracted_maps_legs_to_slots():
    from alerts.copilot_log import prefill_from_extracted
    extracted = {
        "ticker": "SPY", "expiry": "2026-07-17", "entry_price": 1.1,
        "max_profit": 110.0, "max_loss": -390.0,
        "legs": [
            {"action": "BUY", "option_type": "CALL", "strike": 781.0},
            {"action": "SELL", "option_type": "CALL", "strike": 776.0},
            {"action": "BUY", "option_type": "PUT", "strike": 695.0},
            {"action": "SELL", "option_type": "PUT", "strike": 700.0},
        ],
    }
    pf = prefill_from_extracted(extracted)
    assert pf["ticker"] == "SPY"
    assert pf["expiry"] == "2026-07-17"
    assert pf["entry_price"] == "1.1"
    assert pf["bc"] == "781" and pf["sc"] == "776"
    assert pf["bp"] == "695" and pf["sp"] == "700"
