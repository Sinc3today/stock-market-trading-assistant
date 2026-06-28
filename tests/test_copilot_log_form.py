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


def test_build_kwargs_contracts_sets_size():
    from alerts.copilot_log import build_live_trade_kwargs
    kw = build_live_trade_kwargs({"bc": "781", "sc": "776", "bp": "695", "sp": "700",
                                  "entry_price": "1.55", "contracts": "2"})
    assert kw["size"] == 2


def test_build_kwargs_contracts_defaults_to_one():
    from alerts.copilot_log import build_live_trade_kwargs
    kw = build_live_trade_kwargs({"bc": "781", "sc": "776", "entry_price": "1.0"})
    assert kw["size"] == 1


def test_build_kwargs_carries_bot_mark():
    from alerts.copilot_log import build_live_trade_kwargs
    kw = build_live_trade_kwargs({"bc": "781", "sc": "776", "bp": "695", "sp": "700",
                                  "entry_price": "1.55", "contracts": "2",
                                  "bot_mark": "1.00"})
    assert kw["bot_mark"] == 1.00
    # blank bot_mark -> None (manual log with no bot baseline)
    kw2 = build_live_trade_kwargs({"bc": "781", "sc": "776", "entry_price": "1.0"})
    assert kw2["bot_mark"] is None


def test_prefill_from_play_carries_bot_mark():
    from alerts.copilot_log import prefill_from_play
    play = {"ticker": "SPY", "entry_price": 1.00,
            "legs": [{"action": "SELL", "option_type": "CALL", "strike": 771}]}
    assert prefill_from_play(play)["bot_mark"] == "1"


def test_prefill_from_play_fills_strikes_but_not_fill():
    # "I placed it" pre-fills strikes/expiry from the bot play, but leaves the
    # user's actual fill (credit + contracts) blank so they enter what they got.
    from alerts.copilot_log import prefill_from_play
    play = {
        "ticker": "SPY", "strategy": "iron_condor",
        "legs": [
            {"action": "SELL", "option_type": "CALL", "strike": 771.0, "expiration": "2026-07-24"},
            {"action": "BUY",  "option_type": "CALL", "strike": 776.0, "expiration": "2026-07-24"},
            {"action": "SELL", "option_type": "PUT",  "strike": 700.0, "expiration": "2026-07-24"},
            {"action": "BUY",  "option_type": "PUT",  "strike": 695.0, "expiration": "2026-07-24"},
        ],
    }
    pf = prefill_from_play(play)
    assert pf["bc"] == "776" and pf["sc"] == "771"
    assert pf["bp"] == "695" and pf["sp"] == "700"
    assert pf["expiry"] == "2026-07-24"
    # the fields that were wrong before stay blank — user must confirm them
    assert pf["entry_price"] == ""
    assert pf["contracts"] == ""


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
