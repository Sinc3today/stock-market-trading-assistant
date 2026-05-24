"""Tests for kb_validator (Phase 4a items 3+4)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.kb_validator import (
    cap_daily_confidence,
    has_valid_evidence,
    validate_kb_entries,
)


# ── Item 3: confidence cap ──────────────────────────────────

def test_confidence_cap_applies_to_daily_entries_over_threshold():
    entry = {"category": "regime_accuracy", "confidence": 0.95}
    capped, was_capped = cap_daily_confidence(entry, kind="daily")
    assert capped["confidence"] == 0.7
    assert was_capped is True


def test_confidence_cap_skips_daily_entries_under_threshold():
    entry = {"category": "regime_accuracy", "confidence": 0.5}
    capped, was_capped = cap_daily_confidence(entry, kind="daily")
    assert capped["confidence"] == 0.5
    assert was_capped is False


def test_confidence_cap_skips_non_daily_kinds():
    entry = {"category": "hypothesis", "confidence": 0.95}
    capped, was_capped = cap_daily_confidence(entry, kind="hypothesis")
    assert capped["confidence"] == 0.95
    assert was_capped is False


def test_confidence_cap_skips_regime_drift():
    entry = {"category": "market_context", "confidence": 0.85}
    capped, was_capped = cap_daily_confidence(entry, kind="regime_drift")
    assert capped["confidence"] == 0.85
    assert was_capped is False


# ── Item 4: evidence citation ───────────────────────────────

def test_evidence_with_trade_id_passes():
    entry = {"evidence": "Trade AAAA0001 stopped out at $0.45"}
    trade_ids = {"AAAA0001"}
    today_numbers = {587.42}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_sim_trade_id_passes():
    entry = {"evidence": "sim_a3b2c1d4 hit profit target"}
    trade_ids = {"sim_a3b2c1d4"}
    today_numbers = set()
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_today_number_passes():
    entry = {"evidence": "SPY closed at 587.42 above MA200"}
    trade_ids = set()
    today_numbers = {587.42}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_close_float_match_within_tolerance():
    """±0.1% tolerance for float matches (per Q2 confirmation)."""
    entry = {"evidence": "SPY closed at 587.4"}  # vs today's 587.42
    trade_ids = set()
    today_numbers = {587.42}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_integer_exact_match_only():
    """Integers require exact match (per Q2)."""
    entry = {"evidence": "VIX above 15"}
    trade_ids = set()
    today_numbers = {15}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True

    entry2 = {"evidence": "VIX above 14"}  # 14 != 15 exact
    assert has_valid_evidence(entry2, trade_ids, today_numbers) is False


def test_evidence_pure_narrative_fails():
    entry = {"evidence": "Today was a choppy session with the market undecided"}
    trade_ids = set()
    today_numbers = set()
    assert has_valid_evidence(entry, trade_ids, today_numbers) is False


def test_evidence_empty_string_fails():
    entry = {"evidence": ""}
    assert has_valid_evidence(entry, set(), set()) is False


# ── Integration: validate_kb_entries ────────────────────────

def test_validate_caps_daily_and_logs_violations():
    parsed = {
        "kb_entries": [
            {"category": "regime_accuracy", "confidence": 0.92,
             "evidence": "trade AAAA0001 made $150"},
            {"category": "market_context", "confidence": 0.85,
             "evidence": "vague observation about the day"},
        ],
    }
    facts = {
        "trade_ids":      {"AAAA0001"},
        "today_numbers":  {150, 587.42},
    }
    out, metrics = validate_kb_entries(parsed, facts, default_kind="daily")
    assert out["kb_entries"][0]["confidence"] == 0.7  # capped
    assert out["kb_entries"][1]["confidence"] == 0.7  # also capped
    assert metrics["caps_applied"] == 2
    assert metrics["evidence_violations"] == 1
    # Violating entry gets marked but kept (soft enforcement)
    assert out["kb_entries"][1].get("evidence_violation") is True
    assert len(out["kb_entries"]) == 2


# ── KB ID evidence checks (post-review fix) ─────────────────

def test_evidence_with_kb_id_from_recent_kb_passes():
    """KB IDs are bare 10-char hex from KnowledgeBase. Validator must
    look them up against the actual KB IDs provided in facts."""
    entry = {"evidence": "see kb entry abc123def4 for prior pattern"}
    kb_ids = {"abc123def4"}
    assert has_valid_evidence(entry, set(), set(), kb_ids) is True


def test_evidence_with_kb_id_not_in_facts_fails():
    """A 10-char hex that doesn't match any current KB id is treated as
    a generic number candidate, NOT a kb reference."""
    entry = {"evidence": "see kb entry abc123def4 for prior pattern"}
    kb_ids = {"different01"}  # 10-char hex but different
    assert has_valid_evidence(entry, set(), set(), kb_ids) is False


def test_has_valid_evidence_kb_ids_param_optional():
    """Default (no kb_ids arg) must not crash; falls through to other checks."""
    entry = {"evidence": "trade AAAA0001 went well"}
    assert has_valid_evidence(entry, {"AAAA0001"}, set()) is True
