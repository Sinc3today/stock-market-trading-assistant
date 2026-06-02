"""
tests/test_knowledge_base.py -- KBEntry dataclass and KnowledgeBase serialization.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ── stance field tests ─────────────────────────────────────────────────────


def test_kbentry_stance_field_roundtrips(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.knowledge_base import KnowledgeBase, KBEntry
    kb = KnowledgeBase()
    eid = kb.append(KBEntry(date="2026-06-01", category="other", claim="x",
                            strategy="iron_condor", dte_bucket="0DTE",
                            book="learning", stance="disconfirming"))
    rows = kb.recent(days=3650)
    row = [r for r in rows if r["id"] == eid][0]
    assert row["stance"] == "disconfirming"


def test_kbentry_stance_defaults_none():
    from learning.knowledge_base import KBEntry
    assert KBEntry(date="2026-06-01", category="other", claim="x").stance is None
