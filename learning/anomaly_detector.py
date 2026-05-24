"""learning/anomaly_detector.py — Phase 4a item 5.

Pure function: given today's facts, decide whether the reflector should
escalate to Sonnet (anomaly) or stay on phi4 (normal day).

Thresholds are config constants — tune without code changes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


def is_anomalous_day(facts: dict) -> bool:
    """Return True if today warrants Sonnet's reasoning depth.

    Triggers (any single trigger fires = anomaly):
        - stops_today >= REFLECTOR_ANOMALY_STOPS_MIN
        - |prediction_miss_pct| > REFLECTOR_ANOMALY_PRED_MISS_PCT
          (absolute magnitude delta — negative misses handled via abs())
        - REFLECTOR_ANOMALY_NEW_SUBSTRATEGY enabled AND any sub-strategy
          fired for the first time in history
        - REFLECTOR_ANOMALY_REGIME_CHANGE enabled AND regime differs from
          yesterday's prediction

    Missing fact fields default to safe (not anomalous) — facts.get() with
    sensible defaults so callers don't need to populate every key.
    """
    if facts.get("stops_today", 0) >= config.REFLECTOR_ANOMALY_STOPS_MIN:
        return True

    pred_miss = facts.get("prediction_miss_pct", 0.0)
    if abs(float(pred_miss)) > float(config.REFLECTOR_ANOMALY_PRED_MISS_PCT):
        return True

    if config.REFLECTOR_ANOMALY_NEW_SUBSTRATEGY and facts.get("new_substrategies_today"):
        return True

    if config.REFLECTOR_ANOMALY_REGIME_CHANGE and facts.get("regime_changed_today"):
        return True

    return False
