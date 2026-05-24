"""learning/kb_validator.py — Phase 4a items 3+4.

Pure-function validators applied after the reflector's Sonnet/phi4 reply
is parsed. Two checks:

  Item 3: single-day KB entries (kind="daily") have confidence capped at
          config.KB_DAILY_CONFIDENCE_CAP. Higher confidence reserved for
          multi-day-corroborated entries from hypothesis_engine and
          off_hours_learner.

  Item 4: each KB entry's `evidence` string must reference at least one
          concrete piece of evidence — a trade_id, a number from today's
          facts, or a previous KB entry slug. Pure narrative is flagged
          (soft enforcement — entry is kept and marked, not rejected).
"""
from __future__ import annotations

import re
import sys
import os
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


# 8-char uppercase hex (real TradeRecorder ids)
_TRADE_ID_RE = re.compile(r"\b[A-F0-9]{8}\b")
# sim_xxxxxxxx (simulated trade ids)
_SIM_ID_RE   = re.compile(r"\bsim_[0-9a-f]{8,16}\b")
# Numbers (int or float)
_NUMBER_RE   = re.compile(r"-?\d+\.?\d*")


def cap_daily_confidence(entry: dict, kind: str) -> tuple[dict, bool]:
    """Cap confidence at KB_DAILY_CONFIDENCE_CAP for kind=='daily'.

    Returns (entry, was_capped). Entry is mutated in place AND returned
    for chainability.
    """
    if kind != "daily":
        return entry, False
    cap = float(config.KB_DAILY_CONFIDENCE_CAP)
    conf = float(entry.get("confidence", 0.5))
    if conf > cap:
        entry["confidence"] = cap
        return entry, True
    return entry, False


def has_valid_evidence(entry: dict, trade_ids: set, today_numbers: set) -> bool:
    """Item 4: does the entry's `evidence` string reference concrete data?

    Returns True iff `evidence` contains:
      - a trade_id matching today's trades (real or sim_), OR
      - a number matching today's facts (±0.1% for floats, exact for ints), OR
      - a kb_<id> reference to a previous KB entry

    Pure narrative without specifics → False.
    """
    ev = entry.get("evidence") or ""
    if not isinstance(ev, str) or not ev.strip():
        return False

    # Trade id matches
    for m in _TRADE_ID_RE.findall(ev):
        if m in trade_ids:
            return True
    for m in _SIM_ID_RE.findall(ev):
        if m in trade_ids:
            return True

    # KB entry reference
    if re.search(r"\bkb_[a-z0-9_]+\b", ev):
        return True

    # Number matches
    tol_pct = float(config.KB_EVIDENCE_FLOAT_TOLERANCE_PCT) / 100.0
    for token in _NUMBER_RE.findall(ev):
        try:
            num = float(token)
        except ValueError:
            continue
        is_int = "." not in token
        for target in today_numbers:
            if is_int and isinstance(target, int):
                if int(num) == target:
                    return True
            else:
                # Float comparison with relative tolerance
                t = float(target)
                if t == 0:
                    if num == 0:
                        return True
                elif abs(num - t) / abs(t) <= tol_pct:
                    return True
    return False


def validate_kb_entries(parsed: dict, facts: dict,
                        default_kind: str = "daily") -> tuple[dict, dict]:
    """Apply both validators to parsed Sonnet/phi4 JSON.

    Args:
        parsed: dict with "kb_entries" list, as returned by reflector parser.
        facts: dict containing today's `trade_ids` set and `today_numbers` set.
        default_kind: kind to apply when entry doesn't specify (default 'daily').

    Returns (modified_parsed, metrics_dict). Entries are mutated in place.
    The metrics dict has keys: caps_applied, evidence_violations.
    """
    metrics = {"caps_applied": 0, "evidence_violations": 0}
    trade_ids     = facts.get("trade_ids", set())
    today_numbers = facts.get("today_numbers", set())

    for entry in parsed.get("kb_entries", []):
        kind = entry.get("kind", default_kind)
        _, was_capped = cap_daily_confidence(entry, kind)
        if was_capped:
            metrics["caps_applied"] += 1
            logger.info(
                f"kb_validator: capped confidence on '{entry.get('claim','')[:60]}'"
            )
        if not has_valid_evidence(entry, trade_ids, today_numbers):
            metrics["evidence_violations"] += 1
            entry["evidence_violation"] = True
            logger.warning(
                f"kb_validator: evidence-citation violation on '{entry.get('claim','')[:60]}'"
            )
    parsed["_validator_metrics"] = metrics
    return parsed, metrics
