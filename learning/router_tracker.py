"""learning/router_tracker.py -- Flatten a route_explain() decision trace to a
JSONL row and append it under logs/learning/router_track/.

One row per (setup, evaluation). The reflector reads these to narrate the
router's gating behavior; the rollup compacts them to parquet for calibration.
The full JSONL is the lossless source of truth.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


def build_trace_record(setup, trace: dict, now: datetime, source: str) -> dict:
    """Flatten (setup, DecisionTrace) → a JSONL-serializable row.

    `now` must be tz-aware US/Eastern. `source` is "live" or "backfill".
    """
    return {
        "ts":          now.isoformat(),
        "date":        now.date().isoformat(),
        "source":      source,
        "strategy":    setup.strategy,
        "conviction":  setup.conviction,
        "score":       setup.score,
        "direction":   setup.direction,
        "trend":       setup.trend,
        "passed_tier": trace["passed_tier"],
        "accepted":    [a["dte_bucket"] for a in trace["accepted"]],
        "rejected":    trace["rejected"],
    }


def _track_dir() -> str:
    return os.path.join(config.LOG_DIR, "learning", "router_track")


def write_trace(record: dict, day=None) -> str:
    """Append `record` as one JSON line to
    logs/learning/router_track/<day>.jsonl. `day` defaults to record["date"].
    Returns the file path written.
    """
    day_s = (day.isoformat() if hasattr(day, "isoformat") else day) or record["date"]
    path = os.path.join(_track_dir(), f"{day_s}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    return path
