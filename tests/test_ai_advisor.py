"""
tests/test_ai_advisor.py — Test AI Advisor (no API key required)

Run with:
    pytest tests/test_ai_advisor.py -v
"""

import pytest
import sys
import os
import importlib.util

# ── Robust import that works regardless of how pytest is invoked ──
def _load_module(name: str, relative_path: str):
    """Load a module directly from file path — bypasses package resolution."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    full_path = os.path.join(root, relative_path)
    spec = importlib.util.spec_from_file_location(name, full_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.path.insert(0, root)   # ensure sub-imports inside ai_advisor work
    spec.loader.exec_module(mod)
    return mod

_ai_module = _load_module("ai_advisor", os.path.join("alerts", "ai_advisor.py"))
AIAdvisor  = _ai_module.AIAdvisor


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture
def advisor():
    return AIAdvisor()

@pytest.fixture
def mock_score():
    return {
        "final_score": 82, "direction": "bullish",
        "tier": "standard", "alert_emoji": "🟡",
        "confluence_applied": False,
        "layer_scores": {
            "trend":  {"score": 25, "max": 35},
            "setup":  {"score": 22, "max": 35},
            "volume": {"score": 18, "max": 30},
        }
    }

@pytest.fixture
def mock_ma():
    return {
        "ma20": 172.50, "ma50": 168.00, "ma200": 155.00,
        "trend_direction": "bullish", "stack_bullish": True,
        "higher_highs_lows": True, "score": 25,
    }

@pytest.fixture
def mock_trade():
    return {
        "trade_id": "A1B2C3D4", "ticker": "AAPL",
        "strategy": "debit_spread", "direction": "BULLISH",
        "entry_price": 2.30, "exit_price": 3.80,
        "size": 2, "pnl_dollars": 300.0, "pnl_pct": 65.2,
        "outcome": "win", "alert_score": 82,
        "entry_date": "2024-01-15 09:32 AM EST",
    }

@pytest.fixture
def mock_lesson():
    return {
        "trade_id": "A1B2C3D4", "ticker": "AAPL",
        "outcome": "win", "pnl_pct": 65.2,
        "followed_system": True,
        "entry_quality": 4, "exit_quality": 3, "execution_score": 4,
        "emotion_during": "calm",
        "what_went_right": "Waited for volume confirmation",
        "what_went_wrong": "Could have held longer",
        "would_do_differently": "Let the spread go to 75% max profit",
        "lesson_summary": "Trust the system and let winners run",
        "flags": ["system_win", "disciplined_win"],
    }


# ─────────────────────────────────────────
# INIT TESTS
# ─────────────────────────────────────────

def test_advisor_initializes(advisor):
    assert advisor is not None
    print(f"\n✅ Advisor initialized | API key set: {advisor.api_key is not None}")

def test_no_api_key_returns_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a      = AIAdvisor()
    result = a._call_claude("test prompt")
    assert isinstance(result, str)
    assert len(result) > 0
    print(f"\n✅ No API key handled: {result[:60]}")


# ─────────────────────────────────────────
# PROMPT BUILDER TESTS
# ─────────────────────────────────────────

def test_pre_trade_prompt_contains_ticker(advisor, mock_score, mock_ma):
    prompt = advisor._build_pre_trade_prompt(
        "AAPL", mock_score, mock_ma,
        None, None, None, None, None, None, ""
    )
    assert "AAPL"    in prompt
    assert "82"      in prompt
    assert "bullish" in prompt
    print(f"\n✅ Pre-trade prompt contains correct data")

def test_pre_trade_prompt_includes_user_question(advisor, mock_score):
    prompt = advisor._build_pre_trade_prompt(
        "AAPL", mock_score, None, None, None, None, None,
        None, None, "Is the volume strong enough?"
    )
    assert "Is the volume strong enough?" in prompt
    print(f"\n✅ User question included in prompt")

def test_pre_trade_prompt_includes_options(advisor, mock_score):
    options_ctx = {
        "tradeable": True, "strategy": "debit_spread",
        "iv_rank": 25, "iv_assessment": "Low",
        "recommended_dte": 45, "max_loss": "$230",
        "max_profit": "$270", "legs": [],
    }
    prompt = advisor._build_pre_trade_prompt(
        "AAPL", mock_score, None, None, None, None, None,
        options_ctx, None, ""
    )
    assert "OPTIONS CONTEXT" in prompt
    assert "debit_spread"    in prompt
    print(f"\n✅ Options context included in pre-trade prompt")

def test_pre_trade_prompt_includes_history(advisor, mock_score, mock_trade):
    prompt = advisor._build_pre_trade_prompt(
        "AAPL", mock_score, None, None, None, None, None,
        None, [mock_trade], ""
    )
    assert "PAST TRADES" in prompt
    print(f"\n✅ Trade history included in pre-trade prompt")

def test_post_trade_prompt_contains_trade_data(advisor, mock_trade, mock_lesson):
    prompt = advisor._build_post_trade_prompt(
        mock_trade, mock_lesson, None, ""
    )
    assert "AAPL"         in prompt
    assert "debit_spread" in prompt
    assert "300.0"        in prompt
    assert "calm"         in prompt
    print(f"\n✅ Post-trade prompt contains correct trade data")

def test_post_trade_prompt_includes_patterns(advisor, mock_trade, mock_lesson):
    patterns = {
        "total_lessons": 5,
        "followed_win_rate": 75.0, "override_win_rate": 33.0,
        "loss_emotions": {"fomo": 2}, "avg_execution_score": 3.8,
        "top_flags": {"system_win": 3, "early_exit": 2},
    }
    prompt = advisor._build_post_trade_prompt(
        mock_trade, mock_lesson, patterns, ""
    )
    assert "TRADING PATTERNS" in prompt
    assert "75.0"             in prompt
    print(f"\n✅ Patterns included in post-trade prompt")

def test_post_trade_prompt_includes_user_question(advisor, mock_trade):
    prompt = advisor._build_post_trade_prompt(
        mock_trade, None, None, "Did I exit too early?"
    )
    assert "Did I exit too early?" in prompt
    print(f"\n✅ User question included in post-trade prompt")

def test_general_prompt_contains_question(advisor):
    prompt = advisor._build_general_prompt(
        "When should I use a credit spread?", None
    )
    assert "When should I use a credit spread?" in prompt
    assert "trading coach" in prompt.lower()
    print(f"\n✅ General prompt built correctly")

def test_general_prompt_includes_context(advisor):
    ctx    = {"win_rate": 65.0, "total_trades": 20}
    prompt = advisor._build_general_prompt("What should I focus on?", ctx)
    assert "65.0"    in prompt
    assert "context" in prompt.lower()
    print(f"\n✅ Context included in general prompt")


# ─────────────────────────────────────────
# SMOKE TESTS — no API key needed
# ─────────────────────────────────────────

def test_ask_without_api_key_returns_string(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a      = AIAdvisor()
    result = a.ask("What is RSI divergence?")
    assert isinstance(result, str) and len(result) > 0
    print(f"\n✅ ask() returns string without API key")

def test_pre_trade_without_api_key_returns_string(monkeypatch, mock_score):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = AIAdvisor().pre_trade_analysis("AAPL", mock_score)
    assert isinstance(result, str)
    print(f"\n✅ pre_trade_analysis() safe without API key")

def test_post_trade_without_api_key_returns_string(monkeypatch, mock_trade):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = AIAdvisor().post_trade_review(mock_trade)
    assert isinstance(result, str)
    print(f"\n✅ post_trade_review() safe without API key")