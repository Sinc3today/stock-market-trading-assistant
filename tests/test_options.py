"""
tests/test_options.py — Test options layer including spread strategies

Run with:
    pytest tests/test_options.py -v
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from signals.options_layer import OptionsLayer


@pytest.fixture
def options():
    return OptionsLayer()

@pytest.fixture
def bullish_standard():
    return {"final_score": 82, "direction": "bullish", "tier": "standard"}

@pytest.fixture
def bullish_high():
    return {"final_score": 92, "direction": "bullish", "tier": "high_conviction"}

@pytest.fixture
def bearish_standard():
    return {"final_score": 80, "direction": "bearish", "tier": "standard"}

@pytest.fixture
def neutral_score():
    return {"final_score": 78, "direction": "neutral", "tier": "standard"}


# ─────────────────────────────────────────
# STRATEGY SELECTION TESTS
# ─────────────────────────────────────────

def test_low_iv_bullish_gets_debit_spread(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    assert result["tradeable"] is True
    assert result["strategy"] == "debit_spread"
    print(f"\n✅ Low IV bullish → {result['strategy']}")

def test_high_iv_bullish_gets_credit_spread(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=55)
    assert result["tradeable"] is True
    assert result["strategy"] == "credit_spread"
    print(f"\n✅ High IV bullish → {result['strategy']}")

def test_neutral_high_iv_gets_iron_condor(options, neutral_score):
    result = options.analyze("SPY", neutral_score, 450, 455, 445, iv_rank=60)
    assert result["tradeable"] is True
    assert result["strategy"] == "iron_condor"
    print(f"\n✅ Neutral + high IV → {result['strategy']}")

def test_danger_iv_blocked(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=85)
    assert result["tradeable"] is False
    print(f"\n✅ Danger IV blocked: {result['reason']}")

def test_low_score_blocked(monkeypatch):
    """Score below SCORE_ALERT_MINIMUM should block options trading.
    Pins the threshold to 75 so the test is immune to config drift."""
    import config
    monkeypatch.setattr(config, "SCORE_ALERT_MINIMUM", 75)
    low    = {"final_score": 55, "direction": "bullish", "tier": "watchlist"}
    result = OptionsLayer().analyze("AAPL", low, 170, 182, 166, iv_rank=20)
    assert result["tradeable"] is False
    print(f"\n✅ Low score blocked")


# ─────────────────────────────────────────
# LEG STRUCTURE TESTS
# ─────────────────────────────────────────

def test_debit_spread_has_two_legs(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    assert result["leg_count"] == 2
    actions = [l["action"] for l in result["legs"]]
    assert "BUY"  in actions
    assert "SELL" in actions
    print(f"\n✅ Debit spread legs: {[(l['action'], l['option_type'], l['strike']) for l in result['legs']]}")

def test_debit_spread_bullish_uses_calls(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    types = [l["option_type"] for l in result["legs"]]
    assert all(t == "CALL" for t in types)
    print(f"\n✅ Bull debit spread → all CALLs")

def test_debit_spread_bearish_uses_puts(options, bearish_standard):
    result = options.analyze("AAPL", bearish_standard, 170, 158, 174, iv_rank=20)
    assert result["strategy"] == "debit_spread"
    types = [l["option_type"] for l in result["legs"]]
    assert all(t == "PUT" for t in types)
    print(f"\n✅ Bear debit spread → all PUTs")

def test_credit_spread_has_two_legs(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=55)
    assert result["leg_count"] == 2
    print(f"\n✅ Credit spread legs: {[(l['action'], l['option_type'], l['strike']) for l in result['legs']]}")

def test_iron_condor_has_four_legs(options, neutral_score):
    result = options.analyze("SPY", neutral_score, 450, 455, 445, iv_rank=60)
    assert result["leg_count"] == 4
    call_legs = [l for l in result["legs"] if l["option_type"] == "CALL"]
    put_legs  = [l for l in result["legs"] if l["option_type"] == "PUT"]
    assert len(call_legs) == 2
    assert len(put_legs)  == 2
    print(f"\n✅ Iron condor → 4 legs (2 calls, 2 puts)")

def test_buy_strike_lower_than_sell_for_bull_spread(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    buy_leg  = next(l for l in result["legs"] if l["action"] == "BUY")
    sell_leg = next(l for l in result["legs"] if l["action"] == "SELL")
    assert buy_leg["strike"] < sell_leg["strike"]
    print(f"\n✅ Buy ${buy_leg['strike']} < Sell ${sell_leg['strike']}")


# ─────────────────────────────────────────
# RISK / REWARD TESTS
# ─────────────────────────────────────────

def test_debit_spread_has_defined_max_loss(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    assert result["max_loss"] is not None
    assert "$" in str(result["max_loss"])
    print(f"\n✅ Max loss defined: {result['max_loss']}")

def test_iron_condor_has_max_profit(options, neutral_score):
    result = options.analyze("SPY", neutral_score, 450, 455, 445, iv_rank=60)
    assert result["max_profit"] is not None
    print(f"\n✅ Condor max profit: {result['max_profit']}")


# ─────────────────────────────────────────
# DTE + EXIT RULE TESTS
# ─────────────────────────────────────────

def test_swing_gets_45_dte(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166,
                             iv_rank=20, mode="swing")
    assert result["recommended_dte"] == 45
    print(f"\n✅ Swing DTE: {result['recommended_dte']}")

def test_exit_rule_present(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    assert result["exit_rule"] is not None
    assert len(result["exit_rule"]) > 0
    print(f"\n✅ Exit rule: {result['exit_rule']}")

def test_discord_addon_shows_strategy(options, bullish_standard):
    result = options.analyze("AAPL", bullish_standard, 170, 182, 166, iv_rank=20)
    assert "DEBIT SPREAD" in result["discord_addon"].upper()
    assert "BUY"  in result["discord_addon"]
    assert "SELL" in result["discord_addon"]
    print(f"\n✅ Discord addon preview:")
    print(result["discord_addon"])
