"""learning/walls_logger.py -- daily option-wall snapshot logger.

The magnet study (docs/MAGNET_STUDY.md, 2026-07-15) could NOT test the real
options magnets (max pain, OI walls, GEX) because historical open interest
does not exist retroactively in any source we have. Fix: log a daily snapshot
going FORWARD. After ~3-6 months this file supports the real study:
"does SPY close nearer max-pain on expiry days than chance?" /
"do walls act as support/resistance intraday?".

One JSONL line per trading day at logs/walls_history.jsonl:
  {"date": ..., "spot": ..., "max_pain": ..., "call_walls": [...],
   "put_walls": [...], "expiration": ...}

Idempotent per day. Wrapped by the scheduler per Standing Rule #10.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytz
from loguru import logger

import config

PATH = os.path.join(config.LOG_DIR, "walls_history.jsonl")


def _today_iso() -> str:
    return datetime.now(pytz.timezone("US/Eastern")).date().isoformat()


def already_logged(today: str | None = None, path: str = None) -> bool:
    path = path or PATH
    today = today or _today_iso()
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            for line in f:
                if f'"date": "{today}"' in line or f'"date":"{today}"' in line:
                    return True
    except OSError:
        pass
    return False


def snapshot(spot: float, walls: dict, today: str | None = None,
             path: str = None) -> dict | None:
    """Append today's wall snapshot. Returns the record, or None if already
    logged / nothing to log."""
    path = path or PATH
    today = today or _today_iso()
    if not walls or already_logged(today, path):
        return None
    rec = {
        "date": today,
        "spot": round(float(spot), 2) if spot else None,
        "max_pain": walls.get("max_pain"),
        "call_walls": walls.get("call_walls") or [],
        "put_walls": walls.get("put_walls") or [],
        "expiration": walls.get("expiration"),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    logger.info(f"walls_logger: snapshot {today} (max pain {rec['max_pain']})")
    return rec
