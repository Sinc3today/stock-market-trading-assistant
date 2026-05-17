"""
tests/test_learning_kb.py -- KnowledgeBase + Prediction log.
Isolated to tmp_path via monkeypatched config.LOG_DIR.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.knowledge_base import KnowledgeBase, KBEntry
from learning.predictions    import PredictionLog, Prediction


@pytest.fixture
def kb(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return KnowledgeBase()


@pytest.fixture
def plog(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return PredictionLog()


# ── KB ────────────────────────────────────────────────

def test_kb_append_and_read(kb):
    eid = kb.append(KBEntry(
        date=date.today().isoformat(),
        category="regime_accuracy",
        claim="Trending up call confirmed by EOD",
        evidence="SPY +0.8% on +1.2% open gap",
        confidence=0.8,
        tags=["bullish", "confirmed"],
    ))
    assert eid and len(eid) == 10
    rows = kb.all()
    assert len(rows) == 1
    assert rows[0]["category"] == "regime_accuracy"
    assert rows[0]["confidence"] == 0.8


def test_kb_recent_filters_by_date(kb):
    kb.append(KBEntry(date="2025-01-01", category="other", claim="old"))
    kb.append(KBEntry(date=date.today().isoformat(), category="other", claim="new"))
    recent = kb.recent(days=30)
    assert len(recent) == 1
    assert recent[0]["claim"] == "new"


def test_kb_by_category(kb):
    kb.append(KBEntry(date=date.today().isoformat(), category="gate_quality", claim="a"))
    kb.append(KBEntry(date=date.today().isoformat(), category="sizing",       claim="b"))
    assert len(kb.by_category("gate_quality")) == 1
    assert len(kb.by_category("sizing"))       == 1


def test_kb_stats_and_markdown(kb):
    kb.append(KBEntry(date=date.today().isoformat(), category="regime_accuracy", claim="x"))
    kb.append(KBEntry(date=date.today().isoformat(), category="regime_accuracy", claim="y"))
    s = kb.stats()
    assert s["total"] == 2
    assert s["categories"]["regime_accuracy"] == 2
    assert os.path.exists(kb._md_path)
    assert "Knowledge Base" in open(kb._md_path).read()


def test_kb_confidence_clamped(kb):
    kb.append(KBEntry(date="2026-01-01", category="other", claim="x", confidence=2.5))
    row = kb.all()[0]
    assert row["confidence"] == 1.0


# ── Predictions ───────────────────────────────────────

def test_prediction_save_idempotent(plog):
    p = Prediction(
        date=date.today().isoformat(),
        regime="trending_up_calm",
        direction="bullish",
        tradeable=True,
        entry_spy=720.0,
        confidence=0.85,
    )
    plog.save(p)
    plog.save(p)  # second save same date
    assert len(plog.all()) == 1


def test_prediction_resolution_correct(plog):
    today = date.today().isoformat()
    plog.save(Prediction(
        date=today, regime="trending_up_calm",
        direction="bullish", tradeable=True, entry_spy=720.0,
    ))
    plog.mark_resolved(today, actual_close=725.40, outcome="correct", resolution_date=today)
    r = plog.get(today)
    assert r["resolved"] is True
    assert r["outcome"]  == "correct"
    assert r["actual_move_pct"] is not None
    assert r["actual_move_pct"] > 0


def test_prediction_accuracy_aggregates(plog):
    for i, (dir_, close) in enumerate([("bullish", 725.0), ("bullish", 715.0), ("bullish", 730.0)]):
        d = f"2026-04-{10+i:02d}"
        plog.save(Prediction(date=d, regime="x", direction=dir_, tradeable=True, entry_spy=720.0))
        outcome = "correct" if close > 720.0 else "wrong"
        plog.mark_resolved(d, actual_close=close, outcome=outcome, resolution_date=d)
    acc = plog.accuracy(n=30)
    assert acc["sample"] == 3
    assert acc["correct"] == 2
    assert acc["accuracy"] == round(2/3*100, 1)
