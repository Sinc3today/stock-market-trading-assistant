"""
tests/test_learning_off_hours.py -- OffHoursLearner replay + Claude wiring.

Isolates state to tmp_path. Mocks Claude HTTP and (optionally) the CSV path
so tests don't depend on backtests/spy_history.csv being present.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.off_hours_learner import OffHoursLearner
from learning.knowledge_base    import KnowledgeBase


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    # OffHoursLearner falls back to env when api_key is None — strip it so
    # tests can't accidentally hit the live Claude endpoint.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def learner(iso_dirs):
    """Instance with no API key so Claude is never called."""
    return OffHoursLearner(api_key=None)


def test_run_writes_report_when_no_near_misses(learner, iso_dirs, monkeypatch):
    # Force the replay to return an empty list — independent of any CSV.
    monkeypatch.setattr(learner, "_find_near_misses", lambda: [])
    result = learner.run(today=date(2026, 5, 17))

    assert result["near_miss_count"] == 0
    assert result["kb_appended"]     == 0
    report = os.path.join(str(iso_dirs), "learning", "off_hours", "2026-05-17.json")
    assert os.path.exists(report)
    data = json.loads(open(report).read())
    assert data["near_miss_count"] == 0
    assert data["replay_days"]     == 60


def test_run_without_api_key_skips_claude(learner, iso_dirs, monkeypatch):
    fake_misses = [{"date": "2026-05-15", "regime": "trending_up_calm",
                    "adx": 25.1, "vix_used": 16.0, "move_pct": -0.5,
                    "adx_near_threshold": True, "vix_near_threshold": False}]
    monkeypatch.setattr(learner, "_find_near_misses", lambda: fake_misses)

    result = learner.run(today=date(2026, 5, 17))

    # Near-miss recorded but no KB append because api_key is None
    assert result["near_miss_count"] == 1
    assert result["kb_appended"]     == 0
    # Report should still include the near-miss list
    report_path = os.path.join(str(iso_dirs), "learning", "off_hours", "2026-05-17.json")
    data = json.loads(open(report_path).read())
    assert len(data["near_misses"]) == 1


def test_find_near_misses_returns_empty_when_csv_missing(learner, monkeypatch):
    # Point at a CSV path that definitely doesn't exist.
    monkeypatch.chdir("/tmp")
    misses = learner._find_near_misses()
    assert misses == []


def test_ask_claude_parses_kb_entries(iso_dirs, monkeypatch):
    """With a stubbed requests.post returning valid JSON, KB rows are appended."""
    learner = OffHoursLearner(api_key="sk-test-key")
    near = [{"date": "2026-05-15", "regime": "trending_up_calm",
             "adx": 25.0, "vix_used": 16.0, "move_pct": -0.4,
             "adx_near_threshold": True, "vix_near_threshold": False}]

    class FakeResp:
        def raise_for_status(self):  # noqa: D401
            return None
        def json(self):
            return {"content": [{"type": "text", "text": json.dumps({
                "kb_entries": [
                    {"category": "edge_case",
                     "claim":    "ADX just over 25 with negative next-day move",
                     "evidence": "2026-05-15 ADX=25.0 move=-0.4%",
                     "confidence": 0.6,
                     "tags": ["adx-boundary"]},
                ]
            })}]}

    monkeypatch.setattr("requests.post", lambda *a, **kw: FakeResp())
    ids = learner._ask_claude_for_observations("2026-05-17", near)

    assert len(ids) == 1
    rows = KnowledgeBase().all()
    assert any("ADX just over 25" in r["claim"] for r in rows)


def test_ask_claude_handles_malformed_json(iso_dirs, monkeypatch):
    """Claude returns something that isn't JSON — KB is left unchanged."""
    learner = OffHoursLearner(api_key="sk-test-key")
    near = [{"date": "2026-05-15", "regime": "trending_up_calm",
             "adx": 25.0, "vix_used": 16.0, "move_pct": -0.4,
             "adx_near_threshold": True, "vix_near_threshold": False}]

    class FakeResp:
        def raise_for_status(self): return None
        def json(self):
            return {"content": [{"type": "text",
                                 "text": "I'm sorry, I cannot help with that."}]}

    monkeypatch.setattr("requests.post", lambda *a, **kw: FakeResp())
    ids = learner._ask_claude_for_observations("2026-05-17", near)

    assert ids == []
    assert KnowledgeBase().all() == []


def test_ask_claude_swallows_http_errors(iso_dirs, monkeypatch):
    """Network error during Claude call returns [] and logs, doesn't crash."""
    learner = OffHoursLearner(api_key="sk-test-key")
    def boom(*a, **kw):
        raise RuntimeError("connection refused")
    monkeypatch.setattr("requests.post", boom)

    ids = learner._ask_claude_for_observations("2026-05-17", [{"date": "x"}])
    assert ids == []
