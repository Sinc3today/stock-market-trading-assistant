"""
signals/macro_runner.py -- Daily macro snapshot jobs.

Two scheduled jobs, both designed to be passive observers that surface
useful context without mutating the regime detector's tuned thresholds:

    08:55 ET (Mon-Fri)   VIX term structure snapshot + flag-flip detection
    10:00 ET (Mon-Fri)   SPDR sector breadth snapshot + signal-flip detection

On every run:
    1. Fetch a fresh snapshot via the relevant module.
    2. Persist the snapshot to logs/macro/<kind>_latest.json so the web
       dashboard's /macro route can render it without re-fetching.
    3. Compare against the last persisted flag/signal. If it changed,
       append a `market_context` KB entry and (for VIX stress flips)
       ping the notifier so the user sees it on their phone.

State files:
    logs/macro/vix_latest.json     -- last VIX term-structure snapshot
    logs/macro/sector_latest.json  -- last sector breadth snapshot

Both jobs wrap themselves in try/except so a transient CBOE / Polygon
hiccup can never crash the scheduler.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from data.vix_term_structure   import VIXTermStructure
from signals.sector_breadth    import SectorBreadth
from learning.knowledge_base   import KnowledgeBase, KBEntry


_MACRO_DIR = os.path.join(config.LOG_DIR, "macro")


# ── STATE PERSISTENCE ────────────────────────────────────

def _state_path(kind: str) -> str:
    os.makedirs(_MACRO_DIR, exist_ok=True)
    return os.path.join(_MACRO_DIR, f"{kind}_latest.json")


def _load_previous(kind: str) -> Optional[dict]:
    path = _state_path(kind)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"macro_runner: failed to load {path}: {e}")
        return None


def _save_snapshot(kind: str, snapshot: dict) -> None:
    path = _state_path(kind)
    try:
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
    except OSError as e:
        logger.warning(f"macro_runner: failed to save {path}: {e}")


# ── VIX TERM STRUCTURE JOB ───────────────────────────────

# Notify only on transitions into / out of "stress" buckets — keeps the
# noise floor low (no daily ping when nothing changed).
_VIX_STRESS_FLAGS = {"stress", "extreme_stress"}


def run_vix_term_structure_check(post_fn=None) -> dict:
    """
    Fetch today's VIX term structure, persist it, and if the regime flag
    changed, append a KB observation. Stress-bucket flips also notify.
    """
    ts   = VIXTermStructure()
    snap = ts.snapshot()
    today_flag = snap.get("flag", "unknown")

    previous = _load_previous("vix") or {}
    prev_flag = previous.get("flag")

    _save_snapshot("vix", snap)

    if prev_flag is None:
        logger.info(f"macro_runner: first VIX snapshot -- flag={today_flag}")
        return {"changed": False, "flag": today_flag, "snapshot": snap}

    if today_flag == prev_flag or today_flag == "unknown":
        logger.info(f"macro_runner: VIX flag unchanged ({today_flag})")
        return {"changed": False, "flag": today_flag, "snapshot": snap}

    # Flag has flipped.
    entered_stress = today_flag in _VIX_STRESS_FLAGS and prev_flag not in _VIX_STRESS_FLAGS
    left_stress    = prev_flag  in _VIX_STRESS_FLAGS and today_flag not in _VIX_STRESS_FLAGS

    direction = (
        "entered stress" if entered_stress
        else "left stress" if left_stress
        else "shifted"
    )
    claim = (
        f"VIX term structure {direction}: {prev_flag} -> {today_flag} "
        f"(VIX={snap.get('VIX')}, VIX3M={snap.get('VIX3M')}, "
        f"ratio={snap.get('ratio')})"
    )

    try:
        KnowledgeBase().append(KBEntry(
            date       = date.today().isoformat(),
            category   = "market_context",
            claim      = claim[:500],
            evidence   = json.dumps(snap, default=str)[:1000],
            confidence = 0.7,
            source     = "macro_runner.vix",
            tags       = ["vix", "term_structure", today_flag],
        ))
    except Exception as e:
        logger.warning(f"macro_runner: VIX KB append failed: {e}")

    if post_fn and (entered_stress or left_stress):
        emoji = "⚠️" if entered_stress else "✅"
        msg = (
            f"**VIX Term Structure {emoji}**\n"
            f"Flag flipped: {prev_flag} → {today_flag}\n"
            f"VIX={snap.get('VIX')}, VIX3M={snap.get('VIX3M')}, "
            f"ratio={snap.get('ratio')}"
        )
        try:
            post_fn(msg)
        except Exception as e:
            logger.warning(f"macro_runner: VIX notify failed: {e}")

    return {"changed": True, "flag": today_flag, "prev_flag": prev_flag, "snapshot": snap}


# ── SECTOR BREADTH JOB ───────────────────────────────────

def run_sector_breadth_check(polygon_client, post_fn=None) -> dict:
    """
    Fetch today's sector breadth snapshot, persist it, append a KB entry
    when the signal flips, and post a daily Discord briefing.
    """
    sb   = SectorBreadth(polygon_client)
    snap = sb.snapshot()
    today_signal = snap.get("signal", "unknown")

    previous   = _load_previous("sector") or {}
    prev_signal = previous.get("signal")

    _save_snapshot("sector", snap)

    leaders  = snap.get("leaders") or []
    laggards = snap.get("laggards") or []
    dispersion = snap.get("dispersion")

    # Always post the daily briefing (low noise -- once per day).
    if post_fn:
        lead_str = ", ".join(f"{t} {r:+.1f}" for t, r in leaders[:3]) or "—"
        lag_str  = ", ".join(f"{t} {r:+.1f}" for t, r in laggards[:3]) or "—"
        msg = (
            f"**Sector Breadth — {today_signal.upper()}**\n"
            f"Leaders:  {lead_str}\n"
            f"Laggards: {lag_str}\n"
            f"Dispersion: {dispersion}"
        )
        try:
            post_fn(msg)
        except Exception as e:
            logger.warning(f"macro_runner: sector briefing post failed: {e}")

    if prev_signal is None or today_signal == prev_signal or today_signal == "unknown":
        return {"changed": False, "signal": today_signal, "snapshot": snap}

    claim = (
        f"Sector breadth signal flipped: {prev_signal} -> {today_signal} "
        f"(dispersion={dispersion})"
    )
    try:
        KnowledgeBase().append(KBEntry(
            date       = date.today().isoformat(),
            category   = "market_context",
            claim      = claim[:500],
            evidence   = json.dumps({
                "leaders":    [list(x) for x in leaders],
                "laggards":   [list(x) for x in laggards],
                "dispersion": dispersion,
                "signal":     today_signal,
            }, default=str)[:1000],
            confidence = 0.6,
            source     = "macro_runner.sector",
            tags       = ["sectors", "breadth", today_signal],
        ))
    except Exception as e:
        logger.warning(f"macro_runner: sector KB append failed: {e}")

    return {"changed": True, "signal": today_signal, "prev_signal": prev_signal, "snapshot": snap}


# ── SNAPSHOT READERS (for /macro web route) ──────────────

def get_latest_vix() -> Optional[dict]:
    """Return the last persisted VIX snapshot (no fresh fetch)."""
    return _load_previous("vix")


def get_latest_sector() -> Optional[dict]:
    """Return the last persisted sector snapshot (no fresh fetch)."""
    return _load_previous("sector")


# ── SCHEDULER REGISTRATION ───────────────────────────────

def _job_vix(post_fn=None):
    try:
        result = run_vix_term_structure_check(post_fn=post_fn)
        logger.info(f"macro_runner.vix -> {result.get('flag')} "
                    f"(changed={result.get('changed')})")
    except Exception as e:
        logger.exception(f"macro_runner.vix failed: {e}")


def _job_sector(polygon_client, post_fn=None):
    try:
        result = run_sector_breadth_check(polygon_client, post_fn=post_fn)
        logger.info(f"macro_runner.sector -> {result.get('signal')} "
                    f"(changed={result.get('changed')})")
    except Exception as e:
        logger.exception(f"macro_runner.sector failed: {e}")


def register_macro_jobs(scheduler, polygon_client, post_fn=None) -> None:
    """
    Wire both macro snapshot jobs onto the running APScheduler.

        08:55 ET (Mon-Fri)  VIX term structure -- before swing scanner
        10:00 ET (Mon-Fri)  Sector breadth     -- 30 min after open
    """
    from apscheduler.triggers.cron import CronTrigger
    import pytz
    eastern = pytz.timezone("US/Eastern")

    scheduler.add_job(
        _job_vix,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=55, timezone=eastern),
        kwargs={"post_fn": post_fn},
        id="macro_vix",
        name="Macro: VIX term structure",
        replace_existing=True,
    )

    scheduler.add_job(
        _job_sector,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "post_fn": post_fn},
        id="macro_sector",
        name="Macro: sector breadth",
        replace_existing=True,
    )

    logger.info("Macro jobs registered:")
    logger.info("   08:55 ET (Mon-Fri) - VIX term structure")
    logger.info("   10:00 ET (Mon-Fri) - sector breadth")
