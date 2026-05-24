"""
learning/predictions.py -- Daily directional prediction log.

A prediction is the *simplest* artifact the assistant emits each day:
regime + direction + entry price. It is cheap to verify the next day
(did SPY close in the predicted direction?) and gives a fast learning
loop independent of slower multi-day option spread P&L.

File: logs/learning/predictions.jsonl  (append-only, one per day)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

# Flat band (in %) around zero where a skip neither clearly avoided a loss
# nor clearly missed a gain — treated as "neutral" and excluded from the
# right/missed ratio.
SKIP_FLAT_BAND_PCT = 0.10
# For a skipped iron-condor (neutral) setup, a move bigger than this would
# have breached the condor, so skipping it was the right call.
SKIP_CONDOR_BREAKOUT_PCT = 0.50


def score_skip(direction: str, move_pct: float) -> str:
    """
    Was standing down the right call, given how SPY actually moved?
    Returns "right" | "missed" | "neutral". See PredictionLog.skip_quality
    for the rationale per direction.
    """
    if direction == "bullish":
        if move_pct < -SKIP_FLAT_BAND_PCT:  return "right"   # avoided a long-side loss
        if move_pct >  SKIP_FLAT_BAND_PCT:  return "missed"  # left a gain on the table
        return "neutral"
    if direction == "bearish":
        if move_pct >  SKIP_FLAT_BAND_PCT:  return "right"   # avoided a short-side loss
        if move_pct < -SKIP_FLAT_BAND_PCT:  return "missed"
        return "neutral"
    # neutral / iron-condor setup: condor profits in-range, loses on breakout
    if abs(move_pct) > SKIP_CONDOR_BREAKOUT_PCT: return "right"
    return "missed"


@dataclass
class Prediction:
    """One day's directional prediction. Resolved by outcome_resolver."""

    date:             str
    regime:           str
    direction:        str           # bullish / bearish / neutral / skip
    tradeable:        bool
    entry_spy:        float | None  = None
    predicted_target: float | None  = None
    predicted_stop:   float | None  = None
    confidence:       float         = 0.0
    reasons:          list[str]     = field(default_factory=list)
    # Resolution (filled by outcome_resolver)
    resolved:         bool          = False
    resolution_date:  str | None    = None
    actual_close:     float | None  = None
    actual_move_pct:  float | None  = None
    outcome:          str | None    = None   # correct / wrong / partial / skip
    # Per-strategy tags (Phase 2a) — populated by paper_broker, read by
    # downstream Phase 3+ analytics.
    strategy:         str | None    = None   # e.g. "iron_condor", "bull_debit", "put_debit_spread"
    dte_bucket:       str | None    = None   # "0DTE" / "1-3DTE" / "45DTE"
    book:             str | None    = None   # "disciplined" / "learning"


class PredictionLog:
    """Append-only JSONL of one daily prediction."""

    def __init__(self):
        os.makedirs(os.path.join(config.LOG_DIR, "learning"), exist_ok=True)

    @property
    def _path(self) -> str:
        return os.path.join(config.LOG_DIR, "learning", "predictions.jsonl")

    # ── WRITE ─────────────────────────────────────────

    def save(self, prediction: Prediction) -> bool:
        """
        Idempotent per date: replaces an existing same-date entry
        (so re-running the premarket job doesn't double-log).
        """
        rows = self.all()
        rows = [r for r in rows if r.get("date") != prediction.date]
        rows.append(asdict(prediction))
        self._rewrite(rows)
        logger.info(
            f"Prediction saved: {prediction.date} "
            f"{prediction.regime}/{prediction.direction} "
            f"(tradeable={prediction.tradeable})"
        )
        return True

    def mark_resolved(
        self,
        prediction_date: str,
        actual_close:    float,
        outcome:         str,
        resolution_date: str,
    ) -> bool:
        rows = self.all()
        for r in rows:
            if r.get("date") != prediction_date:
                continue
            entry_spy = r.get("entry_spy")
            move_pct  = (
                round((actual_close - entry_spy) / entry_spy * 100, 3)
                if entry_spy else None
            )
            r["resolved"]        = True
            r["resolution_date"] = resolution_date
            r["actual_close"]    = actual_close
            r["actual_move_pct"] = move_pct
            r["outcome"]         = outcome
            self._rewrite(rows)
            logger.info(
                f"Prediction resolved: {prediction_date} -> {outcome} "
                f"(close ${actual_close}, move {move_pct}%)"
            )
            return True
        logger.warning(f"PredictionLog.mark_resolved: no entry for {prediction_date}")
        return False

    # ── READ ──────────────────────────────────────────

    def all(self) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        out = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def get(self, prediction_date: str) -> dict | None:
        return next((r for r in self.all() if r.get("date") == prediction_date), None)

    def unresolved(self) -> list[dict]:
        return [r for r in self.all() if not r.get("resolved")]

    def recent(self, n: int = 30) -> list[dict]:
        return sorted(self.all(), key=lambda r: r.get("date", ""), reverse=True)[:n]

    # Minimum resolved samples required for a sub-strategy to appear in the
    # by_substrategy breakdown.  Prevents tiny-sample noise from polluting
    # the hypothesis engine's targeted tuning suggestions.
    _MIN_SUBSTRATEGY_SAMPLES = 3

    def accuracy(self, n: int = 60, by_substrategy: bool = False) -> dict:
        """Return rolling accuracy over the last n resolved predictions.

        by_substrategy=False (default): returns the aggregate accuracy dict
            {"sample": N, "correct": N, "wrong": N, "accuracy": float}
            Backward-compatible — existing callers are unaffected.

        by_substrategy=True: returns a dict keyed by "strategy:dte_bucket:book"
            for each sub-strategy with >= _MIN_SUBSTRATEGY_SAMPLES resolved
            entries, plus an "all" key containing the same aggregate dict.
            Sub-strategies below the minimum sample floor are omitted to avoid
            noisy signals reaching the hypothesis engine.
        """
        rows = [
            r for r in self.recent(n)
            if r.get("resolved") and r.get("outcome") in ("correct", "wrong")
        ]

        def _stats(subset: list[dict]) -> dict:
            if not subset:
                return {"sample": 0, "correct": 0, "wrong": 0, "accuracy": 0.0}
            correct = sum(1 for r in subset if r["outcome"] == "correct")
            return {
                "sample":   len(subset),
                "correct":  correct,
                "wrong":    len(subset) - correct,
                "accuracy": round(correct / len(subset) * 100, 1),
            }

        if not by_substrategy:
            return _stats(rows)

        # Build per-sub-strategy groups
        groups: dict[str, list[dict]] = {}
        for r in rows:
            strategy   = r.get("strategy")   or "unknown"
            dte_bucket = r.get("dte_bucket") or "unknown"
            book       = r.get("book")        or "unknown"
            key = f"{strategy}:{dte_bucket}:{book}"
            groups.setdefault(key, []).append(r)

        result: dict[str, dict] = {"all": _stats(rows)}
        for key, subset in groups.items():
            if len(subset) >= self._MIN_SUBSTRATEGY_SAMPLES:
                result[key] = _stats(subset)

        return result

    def skip_quality(self, n: int = 60) -> dict:
        """
        Score the bot's STAND-DOWN decisions, kept separate from `accuracy`
        (which scores trades taken) so skips can't inflate the headline
        directional number.

        A skip is the right call when the trade it avoided would have lost:
          - bullish setup skipped → right if SPY fell (a long-biased trade
            would have lost), missed if SPY rose
          - bearish setup skipped → right if SPY rose, missed if it fell
          - neutral (condor) skipped → right if SPY made a big move (the
            condor would have been breached), missed if it stayed in range

        Returns counts + right_pct over resolved skips with a known move.
        """
        rows = [
            r for r in self.recent(n)
            if r.get("outcome") == "skip"
            and isinstance(r.get("actual_move_pct"), (int, float))
        ]
        if not rows:
            return {"sample": 0, "right": 0, "missed": 0, "neutral": 0, "right_pct": 0.0}

        right = missed = neutral = 0
        for r in rows:
            verdict = score_skip(r.get("direction", "neutral"), r["actual_move_pct"])
            if   verdict == "right":  right   += 1
            elif verdict == "missed": missed  += 1
            else:                     neutral += 1

        scored = right + missed
        return {
            "sample":    len(rows),
            "right":     right,
            "missed":    missed,
            "neutral":   neutral,
            "right_pct": round(right / scored * 100, 1) if scored else 0.0,
        }

    # ── HELPERS ───────────────────────────────────────

    def _rewrite(self, rows: list[dict]):
        rows = sorted(rows, key=lambda r: r.get("date", ""))
        with open(self._path, "w") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
