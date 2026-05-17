"""
data/backtest_summary.py -- Read-only aggregator for the /backtest dashboard.

Surfaces four sections without running the full 5-year backtest each
time (that's a minutes-long pandas job — not a per-request operation):

    production_stats()          -- baseline numbers from the last tuned
                                    backtest. Loaded from
                                    logs/backtest_summary.json if present;
                                    otherwise the static defaults from
                                    the tuned production thresholds.

    hypotheses_by_status()      -- everything in logs/learning/hypotheses/*.json
                                    grouped by status (pending / accepted /
                                    rejected / inconclusive). Each entry
                                    has var, proposed_value, deltas.

    prediction_accuracy()       -- rolling N-day accuracy from PredictionLog.

    kb_observations_by_category()
                                -- last 30 days of KB entries grouped by
                                    category, with counts + most-recent
                                    claim per category.

To recompute production_stats:
    python -m backtests.spy_daily_backtest
    # then save the printed report into logs/backtest_summary.json
    # (or call save_production_stats(...) from another script)

This module is intentionally PURE READS — it never mutates the KB,
never calls Polygon, never spawns subprocesses. The /backtest route
loads it on every page render and that's fine.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.knowledge_base import KnowledgeBase
from learning.predictions    import PredictionLog


SUMMARY_FILE = "backtest_summary.json"
HYPOTHESES_DIR = "learning/hypotheses"


# ── PRODUCTION DEFAULTS (5-year SPY tuned-thresholds backtest) ───────
# Source: BUILD_LOG.md + CLAUDE.md "Tuned Thresholds" section.
# These match the last hand-tuned production run. Override by writing
# a fresh logs/backtest_summary.json after re-running the backtest.
_PRODUCTION_DEFAULTS = {
    "source":   "static_defaults",
    "version":  "tuned-2025",
    "years":    5,
    "overview": {
        "sharpe":             1.73,
        "win_rate_pct":       50.3,
        "total_return_pct":   None,    # not recorded in the docs snapshot
        "trade_days":         None,
        "skip_days":          None,
    },
    "by_regime": [
        {"regime": "choppy_low_vol",     "win_rate_pct": 74.1, "tradeable": True,
         "note":   "Iron condor edge — the core profit driver."},
        {"regime": "trending_down_calm", "win_rate_pct": 44.7, "tradeable": True,
         "note":   "Bear debit spread, modest edge."},
        {"regime": "trending_up_calm",   "win_rate_pct": 38.5, "tradeable": True,
         "note":   "Bull debit spread, weak edge."},
        {"regime": "trending_high_vol",  "win_rate_pct": 19.0, "tradeable": False,
         "note":   "Confirmed no edge — skipped in production."},
    ],
    "thresholds": {
        "ADX_TREND_MIN":     25.0,
        "VIX_CALM_MAX":      17.0,
        "IC_RANGE_PCT":      2.5,
    },
}


# ─────────────────────────────────────────
# PRODUCTION STATS
# ─────────────────────────────────────────

def _summary_path() -> str:
    return os.path.join(config.LOG_DIR, SUMMARY_FILE)


def production_stats() -> dict:
    """
    Returns the most-recent tuned-baseline backtest snapshot.

    Order of preference:
      1. logs/backtest_summary.json (if a re-run wrote one)
      2. _PRODUCTION_DEFAULTS (the docs-baseline numbers)
    """
    path = _summary_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            data.setdefault("source", "logs/backtest_summary.json")
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"backtest_summary: failed to load {path}: {e}")
    return dict(_PRODUCTION_DEFAULTS)


def save_production_stats(stats: dict) -> None:
    """
    Persist a freshly-computed backtest summary. Called by the CLI
    that re-runs `backtests.spy_daily_backtest`. Not called from the
    web routes.
    """
    os.makedirs(config.LOG_DIR, exist_ok=True)
    try:
        with open(_summary_path(), "w") as f:
            json.dump(stats, f, indent=2, default=str)
    except OSError as e:
        logger.warning(f"backtest_summary: save failed: {e}")


# ─────────────────────────────────────────
# HYPOTHESES
# ─────────────────────────────────────────

def _hypotheses_dir() -> str:
    return os.path.join(config.LOG_DIR, HYPOTHESES_DIR)


def hypotheses_by_status() -> dict[str, list[dict]]:
    """
    Walk logs/learning/hypotheses/*.json and group by status.

    Each spec ships from learning/hypothesis_engine.py with keys:
      id, status, module, var, baseline_value, proposed_value, rationale.
    After learning/hypothesis_runner.py runs the backtest, the spec
    is augmented with:
      verdict ('accepted' / 'rejected' / 'inconclusive'),
      sharpe_delta, pnl_delta, baseline_sharpe, modified_sharpe.

    We group by verdict (or 'pending' if no verdict yet) and sort
    newest-first within each bucket.
    """
    out: dict[str, list[dict]] = {
        "pending":      [],
        "accepted":     [],
        "rejected":     [],
        "inconclusive": [],
    }
    d = _hypotheses_dir()
    if not os.path.isdir(d):
        return out

    files = sorted(os.listdir(d), reverse=True)
    for name in files:
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path) as f:
                spec = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"backtest_summary: skip bad hypothesis {path}: {e}")
            continue

        verdict = (spec.get("verdict") or "pending").lower()
        if verdict not in out:
            out[verdict] = []
        out[verdict].append(spec)
    return out


# ─────────────────────────────────────────
# PREDICTIONS / KB
# ─────────────────────────────────────────

def prediction_accuracy(window_days: int = 60) -> dict:
    """Wrapper around PredictionLog.accuracy so the /backtest route stays thin."""
    try:
        return PredictionLog().accuracy(n=window_days)
    except Exception as e:
        logger.warning(f"backtest_summary: prediction accuracy failed: {e}")
        return {"sample": 0, "accuracy": 0.0}


def kb_observations_by_category(days: int = 30) -> list[dict]:
    """
    Return [{category, count, latest_claim, latest_date}, ...] for the
    last `days` of KB entries. Sorted by count descending.
    """
    try:
        recent = KnowledgeBase().recent(days)
    except Exception as e:
        logger.warning(f"backtest_summary: KB recent failed: {e}")
        return []

    grouped: dict[str, list[dict]] = {}
    for e in recent:
        cat = e.get("category", "other")
        grouped.setdefault(cat, []).append(e)

    out: list[dict] = []
    for cat, items in grouped.items():
        # Newest entry per category for the "latest" preview
        latest = max(items, key=lambda x: x.get("date", ""))
        out.append({
            "category":     cat,
            "count":        len(items),
            "latest_date":  latest.get("date"),
            "latest_claim": (latest.get("claim") or "")[:200],
        })
    out.sort(key=lambda r: r["count"], reverse=True)
    return out
