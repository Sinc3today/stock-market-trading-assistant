"""
tests/test_learning_reflector.py -- Reflector with mocked Claude.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.reflector    import Reflector
from learning.predictions  import PredictionLog, Prediction
from learning.knowledge_base import KnowledgeBase


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    # Keep tests hermetic: never reach for the live nucbox Ollama fallback.
    monkeypatch.setattr(config, "OLLAMA_FALLBACK_ENABLED", False)
    return tmp_path


def _seed(pred_resolved=True):
    PredictionLog().save(Prediction(
        date=date.today().isoformat(),
        regime="trending_up_calm",
        direction="bullish",
        tradeable=True,
        entry_spy=720.0,
        confidence=0.85,
    ))
    if pred_resolved:
        PredictionLog().mark_resolved(
            date.today().isoformat(),
            actual_close=725.0,
            outcome="correct",
            resolution_date=date.today().isoformat(),
        )


def test_reflect_parses_claude_reply_and_writes_kb(iso, monkeypatch):
    _seed()
    fake_reply = json.dumps({
        "summary": "Bullish call confirmed by +0.7% close.",
        "narrative": "Today the regime was trending_up_calm with ADX 28 ... narrative here.",
        "kb_entries": [
            {
                "category": "regime_accuracy",
                "claim":    "trending_up_calm with ADX>27 and VIX<15 had +EV today",
                "evidence": "SPY 720 -> 725 (+0.69%), prediction correct",
                "confidence": 0.7,
                "tags": ["bullish", "confirmed"],
            },
            {
                "category": "gate_quality",
                "claim":    "0.85 confidence + tradeable gate produced a winning bullish day",
                "evidence": "confidence 0.85, outcome correct",
                "confidence": 0.55,
                "tags": ["gate"],
            },
        ],
    })

    r = Reflector(api_key="fake-key")
    monkeypatch.setattr(r, "_call_claude", lambda prompt: fake_reply)

    result = r.reflect_today()
    assert result["parsed"] is True
    assert len(result["kb_ids"]) == 2

    kb_rows = KnowledgeBase().all()
    assert len(kb_rows) == 2
    cats = {r["category"] for r in kb_rows}
    assert cats == {"regime_accuracy", "gate_quality"}

    md = open(result["markdown"]).read()
    assert "Bullish call confirmed" in md
    assert "regime_accuracy" in md


def test_reflect_handles_malformed_json(iso, monkeypatch):
    _seed()
    r = Reflector(api_key="fake-key")
    monkeypatch.setattr(r, "_call_claude", lambda prompt: "this is not JSON at all")

    result = r.reflect_today()
    assert result["parsed"] is False
    assert result["parse_err"] is not None
    assert len(result["kb_ids"]) == 0
    md = open(result["markdown"]).read()
    assert "parse failed" in md
    assert "this is not JSON" in md


def test_reflect_handles_no_api_key(iso, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed()
    r = Reflector(api_key=None)
    result = r.reflect_today()
    assert result["parsed"] is False
    assert os.path.exists(result["markdown"])
