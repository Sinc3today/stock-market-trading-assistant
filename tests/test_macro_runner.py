"""
tests/test_macro_runner.py -- macro_runner job wrappers, state persistence,
KB integration, and notification firing logic.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import signals.macro_runner as mr
from signals.macro_runner import (
    run_vix_term_structure_check,
    run_sector_breadth_check,
    register_macro_jobs,
    get_latest_vix,
    get_latest_sector,
)
from learning.knowledge_base import KnowledgeBase


@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    """Redirect both LOG_DIR and the module-level _MACRO_DIR to tmp_path."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(mr, "_MACRO_DIR", str(tmp_path / "macro"))
    return tmp_path


# ─────────────────────────────────────────
# VIX job
# ─────────────────────────────────────────

class _StubVIXTS:
    """Snapshot stub injected via monkeypatch."""
    def __init__(self, snap): self._snap = snap
    def snapshot(self):       return self._snap


def _vix_snap(flag, vix=15.0, vix3m=16.0, ratio=0.94):
    return {"VIX9D": 14.0, "VIX": vix, "VIX3M": vix3m, "VIX6M": 17.0,
            "ratio": ratio, "flag": flag, "asof": "2026-05-16T20:00:00"}


def test_vix_first_run_persists_no_kb(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    result = run_vix_term_structure_check()
    assert result["changed"] is False
    assert result["flag"]    == "calm"
    assert get_latest_vix()["flag"] == "calm"
    # No KB entry on first run -- nothing to compare against
    assert KnowledgeBase().all() == []


def test_vix_unchanged_flag_skips_kb(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    run_vix_term_structure_check()                     # first run, persists "calm"
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    result = run_vix_term_structure_check()            # same flag again
    assert result["changed"] is False
    assert KnowledgeBase().all() == []                 # still no KB writes


def test_vix_flip_into_stress_fires_kb_and_pushover(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    run_vix_term_structure_check()                     # seed prev state
    monkeypatch.setattr(
        mr, "VIXTermStructure",
        lambda: _StubVIXTS(_vix_snap("stress", vix=18.0, vix3m=15.0, ratio=1.20)),
    )
    captured = []
    result = run_vix_term_structure_check(post_fn=lambda m: captured.append(m))

    assert result["changed"]   is True
    assert result["prev_flag"] == "calm"
    assert result["flag"]      == "stress"
    rows = KnowledgeBase().all()
    assert len(rows) == 1
    assert rows[0]["category"] == "market_context"
    assert "vix" in (rows[0].get("tags") or [])
    # Pushover fired exactly once on the stress entry
    assert len(captured) == 1
    assert "entered stress" not in captured[0].lower() or "stress" in captured[0].lower()


def test_vix_recovery_from_stress_fires_kb_and_pushover(iso_logs, monkeypatch):
    monkeypatch.setattr(
        mr, "VIXTermStructure",
        lambda: _StubVIXTS(_vix_snap("stress", vix=18.0, vix3m=15.0, ratio=1.20)),
    )
    run_vix_term_structure_check()
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    captured = []
    result = run_vix_term_structure_check(post_fn=lambda m: captured.append(m))
    assert result["changed"] is True
    assert len(captured) == 1   # leaving stress also notifies


def test_vix_shift_within_calm_buckets_doesnt_notify(iso_logs, monkeypatch):
    """calm -> cautious is a flip but not a stress event; KB but no post."""
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    run_vix_term_structure_check()
    monkeypatch.setattr(
        mr, "VIXTermStructure",
        lambda: _StubVIXTS(_vix_snap("cautious", vix=15.5, vix3m=14.5, ratio=1.07)),
    )
    captured = []
    run_vix_term_structure_check(post_fn=lambda m: captured.append(m))
    assert len(KnowledgeBase().all()) == 1
    assert captured == []   # no Pushover ping for a non-stress flip


def test_vix_unknown_flag_doesnt_overwrite_meaning(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "VIXTermStructure", lambda: _StubVIXTS(_vix_snap("calm")))
    run_vix_term_structure_check()
    monkeypatch.setattr(
        mr, "VIXTermStructure",
        lambda: _StubVIXTS({"VIX9D": None, "VIX": None, "VIX3M": None,
                             "VIX6M": None, "ratio": None, "flag": "unknown",
                             "asof": "x"}),
    )
    result = run_vix_term_structure_check()
    assert result["changed"] is False
    assert KnowledgeBase().all() == []


# ─────────────────────────────────────────
# Sector job
# ─────────────────────────────────────────

def _sector_snap(signal, dispersion=2.0):
    return {
        "leaders":    [("XLK", 4.2), ("XLY", 3.0), ("XLF", 2.5)],
        "laggards":   [("XLE", -3.5), ("XLU", -2.1), ("XLP", -1.4)],
        "dispersion": dispersion,
        "signal":     signal,
        "rs":         {"XLK": 4.2},
        "asof":       "2026-05-16T20:00:00",
        "horizon":    20,
    }


class _StubSector:
    def __init__(self, snap): self._snap = snap
    def snapshot(self):       return self._snap


def test_sector_first_run_persists_no_kb(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "SectorBreadth",
                        lambda polygon: _StubSector(_sector_snap("rotating")))
    captured = []
    result = run_sector_breadth_check(polygon_client=None,
                                       post_fn=lambda m: captured.append(m))
    assert result["changed"] is False
    assert get_latest_sector()["signal"] == "rotating"
    # Daily briefing fires on every run, including first
    assert len(captured) == 1 and "Sector Breadth" in captured[0]
    assert KnowledgeBase().all() == []


def test_sector_flip_appends_kb(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "SectorBreadth",
                        lambda polygon: _StubSector(_sector_snap("rotating", dispersion=2.0)))
    run_sector_breadth_check(polygon_client=None)
    monkeypatch.setattr(mr, "SectorBreadth",
                        lambda polygon: _StubSector(_sector_snap("dispersed", dispersion=3.5)))
    captured = []
    result = run_sector_breadth_check(polygon_client=None,
                                       post_fn=lambda m: captured.append(m))
    assert result["changed"]    is True
    assert result["prev_signal"] == "rotating"
    assert result["signal"]      == "dispersed"
    rows = KnowledgeBase().all()
    assert len(rows) == 1
    assert rows[0]["category"] == "market_context"
    assert "sectors" in (rows[0].get("tags") or [])
    # Daily briefing fires on the flip run too
    assert len(captured) == 1


def test_sector_unchanged_skips_kb(iso_logs, monkeypatch):
    monkeypatch.setattr(mr, "SectorBreadth",
                        lambda polygon: _StubSector(_sector_snap("rotating")))
    run_sector_breadth_check(polygon_client=None)
    monkeypatch.setattr(mr, "SectorBreadth",
                        lambda polygon: _StubSector(_sector_snap("rotating")))
    result = run_sector_breadth_check(polygon_client=None)
    assert result["changed"] is False
    assert KnowledgeBase().all() == []


# ─────────────────────────────────────────
# Scheduler registration
# ─────────────────────────────────────────

class _FakeScheduler:
    def __init__(self): self.jobs = []
    def add_job(self, fn, trigger, **kwargs):
        self.jobs.append({"fn": fn, "trigger": trigger, **kwargs})


def test_register_macro_jobs_adds_two_jobs():
    s = _FakeScheduler()
    register_macro_jobs(s, polygon_client=None, post_fn=None)
    assert len(s.jobs) == 2
    ids = {j["id"] for j in s.jobs}
    assert ids == {"macro_vix", "macro_sector"}


def test_register_macro_jobs_passes_polygon_to_sector_only():
    s = _FakeScheduler()
    polygon = object()
    post_fn = lambda m: None
    register_macro_jobs(s, polygon_client=polygon, post_fn=post_fn)
    sector = next(j for j in s.jobs if j["id"] == "macro_sector")
    vix    = next(j for j in s.jobs if j["id"] == "macro_vix")
    assert sector["kwargs"]["polygon_client"] is polygon
    assert sector["kwargs"]["post_fn"]        is post_fn
    assert "polygon_client" not in vix["kwargs"]   # VIX uses CBOE, no polygon needed
    assert vix["kwargs"]["post_fn"]           is post_fn
