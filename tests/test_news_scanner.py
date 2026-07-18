"""Tests for NewsScanner briefing persistence.

Regression guard for the 2026-06-15 freeze that truncated
logs/news_briefings.json to 0 bytes. An empty/corrupt file made
_save_briefing throw on json.load and silently refuse every subsequent
save (self-perpetuating), so no briefing persisted for a month. The fix:
treat empty/corrupt as "start fresh" and write atomically.
"""
import json
import os

import pytest

from scanners.news_scanner import NewsScanner


@pytest.fixture
def scanner(tmp_path):
    s = NewsScanner.__new__(NewsScanner)  # skip network/config in __init__
    s._news_log_path = str(tmp_path / "news_briefings.json")
    return s


def test_save_briefing_writes_to_fresh_file(scanner):
    scanner._save_briefing({"type": "morning", "summary": "hello"})
    with open(scanner._news_log_path) as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["summary"] == "hello"


def test_save_briefing_recovers_from_empty_file(scanner):
    # The exact corruption state from the June-15 freeze: 0-byte file.
    open(scanner._news_log_path, "w").close()
    assert os.path.getsize(scanner._news_log_path) == 0

    scanner._save_briefing({"type": "midday", "summary": "recovered"})

    with open(scanner._news_log_path) as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["summary"] == "recovered"


def test_save_briefing_recovers_from_corrupt_json(scanner):
    with open(scanner._news_log_path, "w") as f:
        f.write("{not valid json at all")

    scanner._save_briefing({"type": "eod", "summary": "after corrupt"})

    with open(scanner._news_log_path) as f:
        data = json.load(f)
    assert data[-1]["summary"] == "after corrupt"


def test_save_briefing_appends_and_caps_at_90(scanner):
    for i in range(95):
        scanner._save_briefing({"type": "morning", "summary": f"b{i}"})
    with open(scanner._news_log_path) as f:
        data = json.load(f)
    assert len(data) == 90
    assert data[0]["summary"] == "b5"    # oldest 5 dropped
    assert data[-1]["summary"] == "b94"


def test_get_recent_briefings_after_save(scanner):
    for i in range(3):
        scanner._save_briefing({"type": "morning", "summary": f"b{i}"})
    recent = scanner.get_recent_briefings(limit=2)
    assert [b["summary"] for b in recent] == ["b2", "b1"]  # most-recent first
