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
from learning.predictions    import PredictionLog, score_skip
from learning.paper_broker   import AUTO_TAG
from journal.trade_recorder  import TradeRecorder


SUMMARY_FILE = "backtest_summary.json"
HYPOTHESES_DIR = "learning/hypotheses"


# ── PRODUCTION DEFAULTS (5-year SPY tuned-thresholds backtest) ───────
# Source: BUILD_LOG.md + CLAUDE.md "Tuned Thresholds" section.
# These match the last hand-tuned production run. Override by writing
# a fresh logs/backtest_summary.json after re-running the backtest.
_PRODUCTION_DEFAULTS = {
    "source":   "static_defaults",
    "version":  "tuned-2026-05 (over-extension cap)",
    "years":    5,
    "overview": {
        "sharpe":             3.06,
        "win_rate_pct":       59.4,
        "total_return_pct":   None,    # not recorded in the docs snapshot
        "trade_days":         495,
        "skip_days":          525,
    },
    "by_regime": [
        {"regime": "choppy_low_vol",     "win_rate_pct": 74.1, "tradeable": True,
         "note":   "Iron condor edge — the core profit driver."},
        {"regime": "trending_down_calm", "win_rate_pct": 44.7, "tradeable": True,
         "note":   "Bear debit spread, modest edge."},
        {"regime": "trending_up_calm",   "win_rate_pct": 59.4, "tradeable": True,
         "note":   "Bull debit, now capped at <9% above 200MA (over-extended skipped)."},
        {"regime": "trending_high_vol",  "win_rate_pct": 19.0, "tradeable": False,
         "note":   "Confirmed no edge — skipped in production."},
    ],
    "thresholds": {
        "ADX_TREND_MIN":          25.0,
        "VIX_CALM_MAX":           17.0,
        "EXTENDED_TREND_MAX_PCT":  9.0,
        "IC_RANGE_PCT":            2.5,
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


def skip_quality(window_days: int = 60) -> dict:
    """Wrapper around PredictionLog.skip_quality — the bot's stand-down hit rate."""
    try:
        return PredictionLog().skip_quality(n=window_days)
    except Exception as e:
        logger.warning(f"backtest_summary: skip quality failed: {e}")
        return {"sample": 0, "right": 0, "missed": 0, "neutral": 0, "right_pct": 0.0}


def recent_predictions(n: int = 14) -> list[dict]:
    """
    Return up to `n` most-recent predictions (resolved + unresolved) for
    the /learning live track-record page. Each row is normalised to the
    keys the template expects so the renderer stays dumb.
    """
    try:
        rows = PredictionLog().recent(n=n)
    except Exception as e:
        logger.warning(f"backtest_summary: recent_predictions failed: {e}")
        return []

    out: list[dict] = []
    for r in rows:
        move = r.get("actual_move_pct")
        move_num = move if isinstance(move, (int, float)) else None
        # For skips, derive whether standing down was the right call.
        skip_verdict = None
        if r.get("outcome") == "skip" and move_num is not None:
            skip_verdict = score_skip(r.get("direction", "neutral"), move_num)
        out.append({
            "date":          r.get("date"),
            "regime":        r.get("regime"),
            "direction":     r.get("direction"),
            "confidence":    r.get("confidence"),
            "tradeable":     r.get("tradeable"),
            "entry_spy":     r.get("entry_spy"),
            "actual_close":  r.get("actual_close"),
            "actual_move_pct": move_num,
            "outcome":       r.get("outcome"),   # "correct" | "wrong" | "skip" | None
            "skip_verdict":  skip_verdict,       # "right" | "missed" | "neutral" | None
            "resolved":      bool(r.get("resolved")),
        })
    return out


def paper_trade_stats() -> dict:
    """
    Aggregate Claude's auto-paper-traded positions (tagged [AUTO-PAPER] in
    notes_entry). Returns open / closed counts, total realized P&L, win
    rate on closed positions, and a chronological closed-trade list for
    cumulative-P&L display.

    Never raises — every external dependency is wrapped.
    """
    empty = {
        "open":         0,
        "closed":       0,
        "wins":         0,
        "losses":       0,
        "win_rate_pct": 0.0,
        "total_pnl":    0.0,
        "closed_trades": [],
        "open_trades":   [],
    }
    try:
        trades = TradeRecorder().get_all_trades()
    except Exception as e:
        logger.warning(f"backtest_summary: TradeRecorder load failed: {e}")
        return empty

    auto = [t for t in trades if AUTO_TAG in (t.get("notes_entry") or "")]
    if not auto:
        return empty

    open_t   = [t for t in auto if t.get("outcome") == "open"]
    closed_t = sorted(
        [t for t in auto if t.get("outcome") != "open"],
        key=lambda t: t.get("exit_date") or t.get("entry_date") or "",
    )
    wins = [t for t in closed_t if t.get("outcome") == "win"]
    pnls = [t.get("pnl_dollars") or 0.0 for t in closed_t]

    # Cumulative P&L series for the sparkline on /learning. Starts at 0
    # (the y=0 baseline before any trade closed) and appends the running
    # total after each closed trade in chronological order.
    cum = 0.0
    cum_series: list[float] = [0.0]
    for p in pnls:
        cum += float(p or 0.0)
        cum_series.append(round(cum, 2))

    return {
        "open":               len(open_t),
        "closed":              len(closed_t),
        "wins":                len(wins),
        "losses":              len(closed_t) - len(wins),
        "win_rate_pct":        round(len(wins) / len(closed_t) * 100, 1) if closed_t else 0.0,
        "total_pnl":           round(sum(pnls), 2),
        "closed_trades":       closed_t,
        "open_trades":         open_t,
        "cumulative_pnl_series": cum_series,
    }


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
