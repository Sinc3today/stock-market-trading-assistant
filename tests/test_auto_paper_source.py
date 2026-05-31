"""
tests/test_auto_paper_source.py -- structured auto-paper identification.

The fragile bit being fixed: consumers used to detect bot-generated paper
trades with `AUTO_TAG in notes_entry`. The Phase 3 event-driven entry path
wrote `[AUTO-PAPER 2026-05-27] event-driven entry` (date INSIDE the brackets),
so the literal `[AUTO-PAPER]` substring was absent and those trades fell
through every resolver/exit filter.

`is_auto_paper(trade)` replaces the substring check: it prefers a structured
`source` field and falls back to the legacy notes tag for trades created
before the field existed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.paper_broker import AUTO_SOURCE, is_auto_paper


def test_structured_source_is_recognized():
    assert is_auto_paper({"source": AUTO_SOURCE}) is True


def test_legacy_tag_only_still_recognized():
    # Trades created before the source field carry only the notes tag.
    assert is_auto_paper({"notes_entry": "[AUTO-PAPER] regime=foo"}) is True


def test_broken_event_driven_note_without_source_is_not_recognized():
    # Documents the original bug: date inside the brackets breaks the substring.
    assert is_auto_paper(
        {"notes_entry": "[AUTO-PAPER 2026-05-27] event-driven entry"}
    ) is False


def test_source_field_rescues_broken_note():
    # The fix: even with the broken legacy note, the source field identifies it.
    assert is_auto_paper(
        {"source": AUTO_SOURCE, "notes_entry": "[AUTO-PAPER 2026-05-27] event-driven entry"}
    ) is True


def test_non_auto_trade_is_not_recognized():
    assert is_auto_paper({"notes_entry": "manual fill", "source": "manual"}) is False
    assert is_auto_paper({}) is False
