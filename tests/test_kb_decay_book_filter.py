"""tests/test_kb_decay_book_filter.py -- T3#12 KB confidence decay + T3#13 book filter."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_effective_confidence_halves_at_half_life():
    from learning.knowledge_base import KnowledgeBase
    e = {"confidence": 0.8, "date": "2026-04-10"}
    today = date(2026, 7, 9)   # 90 days later
    assert KnowledgeBase.effective_confidence(e, today) == pytest.approx(0.4, abs=0.01)


def test_effective_confidence_fresh_entry_unchanged():
    from learning.knowledge_base import KnowledgeBase
    e = {"confidence": 0.7, "date": "2026-07-09"}
    assert KnowledgeBase.effective_confidence(e, date(2026, 7, 9)) == pytest.approx(0.7)


def test_recent_includes_effective_confidence(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.knowledge_base import KnowledgeBase, KBEntry
    kb = KnowledgeBase()
    kb.append(KBEntry(date=date.today().isoformat(), category="market_context",
                      claim="test claim with numbers 42",
                      evidence="trade AB12CD34", confidence=0.6))
    rows = kb.recent(days=7)
    assert rows and "effective_confidence" in rows[0]


def test_accuracy_book_filter(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.predictions import PredictionLog, Prediction
    pl = PredictionLog()
    for i, (book, outcome) in enumerate([("disciplined", "correct"),
                                         ("learning", "wrong"),
                                         (None, "correct")]):   # None = legacy
        p = Prediction(date=f"2026-07-0{i+1}", regime="x", direction="bullish",
                       tradeable=True, entry_spy=700.0, book=book)
        pl.save(p)
        pl.mark_resolved(p.date, actual_close=705.0, outcome=outcome,
                         resolution_date=p.date)
    acc = pl.accuracy(n=30, book="disciplined")
    # disciplined + legacy(None->disciplined) count; the learning-book wrong is excluded
    assert acc["sample"] == 2 and acc["correct"] == 2