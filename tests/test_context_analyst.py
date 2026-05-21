"""
tests/test_context_analyst.py -- local-first morning context read + escalation.

LLM backends are mocked; no network. Verifies the local-first/escalate
orchestration, JSON normalisation, and graceful failure.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals import context_analyst as ca
from signals.context_analyst import ContextAnalyst


def _json(bias="bullish", conf=0.8):
    return json.dumps({
        "bias": bias, "confidence": conf,
        "key_levels": ["above 590", "watch 585"],
        "risk_flags": ["none"], "summary": "Calm uptrend continuation expected.",
    })


@pytest.fixture
def today():
    return date(2026, 5, 21)


# ── Local-first / escalation orchestration ─────────────

def test_high_confidence_local_skips_escalation(monkeypatch, today):
    calls = {"local": 0, "anthropic": 0}
    monkeypatch.setattr(ca.llm_client, "call_local",
                        lambda *a, **k: calls.__setitem__("local", calls["local"] + 1) or _json(conf=0.8))
    monkeypatch.setattr(ca.llm_client, "call_anthropic",
                        lambda *a, **k: calls.__setitem__("anthropic", calls["anthropic"] + 1) or _json())
    out = ContextAnalyst(api_key="sk-test").analyze(today=today)
    assert out["source"] == "local"
    assert out["bias"] == "bullish"
    assert calls["anthropic"] == 0          # no escalation when local is confident


def test_low_confidence_local_escalates(monkeypatch, today):
    monkeypatch.setattr(ca.llm_client, "call_local", lambda *a, **k: _json(conf=0.3))
    monkeypatch.setattr(ca.llm_client, "call_anthropic", lambda *a, **k: _json(bias="bearish", conf=0.7))
    out = ContextAnalyst(api_key="sk-test").analyze(today=today)
    assert out["source"] == "anthropic"
    assert out["bias"] == "bearish"


def test_unparseable_local_escalates(monkeypatch, today):
    monkeypatch.setattr(ca.llm_client, "call_local", lambda *a, **k: "garbage no json")
    monkeypatch.setattr(ca.llm_client, "call_anthropic", lambda *a, **k: _json(conf=0.7))
    out = ContextAnalyst(api_key="sk-test").analyze(today=today)
    assert out["source"] == "anthropic"


def test_both_backends_fail_returns_neutral_default(monkeypatch, today):
    monkeypatch.setattr(ca.llm_client, "call_local", lambda *a, **k: "")
    monkeypatch.setattr(ca.llm_client, "call_anthropic", lambda *a, **k: "")
    out = ContextAnalyst(api_key="sk-test").analyze(today=today)
    assert out["bias"] == "neutral"
    assert out["confidence"] == 0.0
    assert out["source"] == "none"


# ── Parsing / normalisation ────────────────────────────

def test_parse_clamps_and_normalises():
    raw = json.dumps({"bias": "BULLISH", "confidence": 1.7,
                      "key_levels": [1, 2, 3, 4, 5], "risk_flags": ["a", "b", "c", "d"],
                      "summary": "x" * 400})
    d = ContextAnalyst._parse(raw)
    assert d["bias"] == "bullish"
    assert d["confidence"] == 1.0          # clamped
    assert len(d["key_levels"]) == 3       # truncated
    assert len(d["risk_flags"]) == 3
    assert len(d["summary"]) == 200


def test_parse_rejects_missing_fields():
    assert ContextAnalyst._parse(json.dumps({"summary": "no bias here"})) is None
    assert ContextAnalyst._parse("") is None


def test_unknown_bias_becomes_neutral():
    d = ContextAnalyst._parse(json.dumps({"bias": "sideways", "confidence": 0.5}))
    assert d["bias"] == "neutral"


# ── Event-day risk handling (prompt carries events) ────

def test_prompt_includes_events_and_gap(today):
    p = ContextAnalyst._build_prompt(
        [{"event": "FOMC", "days_away": 0}], 0.42, ["Fed decision at 2pm"], today)
    assert "FOMC" in p
    assert "+0.42%" in p
    assert "Fed decision at 2pm" in p
