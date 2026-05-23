"""Phase 1: KBEntry has strategy/dte_bucket/book optional tags; KnowledgeBase
exposes search() that filters by them."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from learning.knowledge_base import KBEntry, KnowledgeBase


def test_kbentry_new_fields_default_to_none():
    e = KBEntry(date="2026-05-23", category="regime_accuracy", claim="x")
    assert e.strategy   is None
    assert e.dte_bucket is None
    assert e.book       is None


def test_kbentry_new_fields_accept_strings():
    e = KBEntry(date="2026-05-23", category="exit_timing", claim="x",
                strategy="iron_condor", dte_bucket="0DTE", book="learning")
    assert e.strategy   == "iron_condor"
    assert e.dte_bucket == "0DTE"
    assert e.book       == "learning"


def test_kbentry_roundtrip_through_jsonl(tmp_path, monkeypatch):
    """Existing entries lack the new fields; they must still round-trip."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    kb = KnowledgeBase()
    # Tagged
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="tagged",
                      strategy="iron_condor", dte_bucket="0DTE", book="disciplined"))
    # Untagged (old shape)
    kb.append(KBEntry(date="2026-05-23", category="regime_accuracy", claim="untagged"))
    rows = kb.all()
    assert len(rows) == 2
    tagged   = next(r for r in rows if r["claim"] == "tagged")
    untagged = next(r for r in rows if r["claim"] == "untagged")
    assert tagged["strategy"]      == "iron_condor"
    assert untagged.get("strategy") is None


def test_search_filters_by_strategy(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    kb = KnowledgeBase()
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="a",
                      strategy="iron_condor", dte_bucket="0DTE", book="disciplined"))
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="b",
                      strategy="bull_debit", dte_bucket="1-3DTE", book="disciplined"))
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="c"))  # untagged

    only_condor = kb.search(strategy="iron_condor")
    assert [r["claim"] for r in only_condor] == ["a"]

    only_disciplined = kb.search(book="disciplined")
    assert sorted([r["claim"] for r in only_disciplined]) == ["a", "b"]

    only_0dte_condor_disciplined = kb.search(strategy="iron_condor",
                                              dte_bucket="0DTE",
                                              book="disciplined")
    assert [r["claim"] for r in only_0dte_condor_disciplined] == ["a"]


def test_search_no_filter_returns_all(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    kb = KnowledgeBase()
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="x",
                      strategy="iron_condor"))
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="y"))
    assert len(kb.search()) == 2   # no filter = all
