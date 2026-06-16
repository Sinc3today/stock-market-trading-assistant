"""tests/test_play_vision.py -- screenshot -> play extraction (pure helpers).

The network call to Claude is isolated; these test the prompt/message shape and
the reply parser that normalizes Claude's JSON into our canonical play dict.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_build_messages_has_image_then_text():
    from alerts.play_vision import build_messages
    msgs = build_messages("BASE64DATA", "image/png")
    assert isinstance(msgs, list) and len(msgs) == 1
    content = msgs[0]["content"]
    # image block first, instruction text second
    assert content[0]["type"] == "image"
    assert content[0]["source"] == {
        "type": "base64", "media_type": "image/png", "data": "BASE64DATA"}
    assert content[1]["type"] == "text"
    assert "JSON" in content[1]["text"]


def test_parse_reply_strips_code_fence_and_normalizes_legs():
    from alerts.play_vision import parse_reply
    raw = """```json
    {
      "ticker": "SPY",
      "strategy": "Iron Condor",
      "expiration": "2026-07-17",
      "net_credit": 1.10,
      "max_profit": 110,
      "max_loss": -390,
      "legs": [
        {"action": "buy",  "type": "call", "strike": 781},
        {"action": "sell", "type": "call", "strike": 776},
        {"action": "sell", "type": "put",  "strike": 700},
        {"action": "buy",  "type": "put",  "strike": 695}
      ]
    }
    ```"""
    play = parse_reply(raw)
    assert play["ticker"] == "SPY"
    assert play["strategy"] == "iron_condor"
    assert play["expiry"] == "2026-07-17"
    assert play["entry_price"] == 1.10
    assert play["max_profit"] == 110.0
    assert play["max_loss"] == -390.0
    assert len(play["legs"]) == 4
    leg = play["legs"][0]
    assert leg == {"action": "BUY", "option_type": "CALL",
                   "strike": 781.0, "expiry": "2026-07-17"}


def test_parse_reply_tolerates_missing_and_aliased_fields():
    from alerts.play_vision import parse_reply
    play = parse_reply('{"underlying": "SPY", "limit_price": 0.85, "legs": []}')
    assert play["ticker"] == "SPY"          # underlying -> ticker
    assert play["entry_price"] == 0.85      # limit_price -> entry_price
    assert play["expiry"] is None
    assert play["max_profit"] is None
    assert play["legs"] == []


def test_parse_reply_raises_on_unparseable():
    from alerts.play_vision import parse_reply
    import pytest
    with pytest.raises(ValueError):
        parse_reply("I could not read this screenshot.")
