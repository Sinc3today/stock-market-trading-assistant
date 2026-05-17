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

    def accuracy(self, n: int = 60) -> dict:
        rows = [r for r in self.recent(n) if r.get("resolved") and r.get("outcome") in ("correct", "wrong")]
        if not rows:
            return {"sample": 0, "accuracy": 0.0}
        correct = sum(1 for r in rows if r["outcome"] == "correct")
        return {
            "sample":   len(rows),
            "correct":  correct,
            "wrong":    len(rows) - correct,
            "accuracy": round(correct / len(rows) * 100, 1),
        }

    # ── HELPERS ───────────────────────────────────────

    def _rewrite(self, rows: list[dict]):
        rows = sorted(rows, key=lambda r: r.get("date", ""))
        with open(self._path, "w") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
