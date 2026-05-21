"""
scheduler/spy_daily_scheduler.py — SPY Daily Jobs for APScheduler

Adds three jobs to your existing APScheduler instance in main.py.
Do NOT run this as a standalone process — it integrates into main.py.

Jobs:
    09:15 ET  Pre-market  → Build daily play, log plan, post to Discord
    16:30 ET  Close snap  → Attach SPY close price to today's plan

Integration in main.py (see bottom of this file for exact lines):
    from scheduler.spy_daily_scheduler import register_spy_jobs
    register_spy_jobs(scheduler, polygon_client, discord_channel_id)

Posting uses post_message_sync() — a thin helper added to discord_bot.py
that posts a plain string to a channel without requiring an alert dict.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
from loguru import logger
import pytz

import config
from signals.spy_daily_strategy import SPYDailyStrategy
from signals.morning_briefer    import MorningBriefer
from journal.plan_logger         import PlanLogger
from data.earnings_calendar     import EarningsCalendar
from data.earnings_history      import EarningsHistory

ET = pytz.timezone("US/Eastern")


# ─────────────────────────────────────────
# JOB FUNCTIONS
# ─────────────────────────────────────────

def job_spy_premarket(
    polygon_client,
    vix_client,
    ivr_client,
    post_fn,              # post_message_sync(message: str) — see discord_bot.py
    event_calendar=None,
):
    """
    09:15 ET — Build the morning brief and post to Discord + Pushover.

    Uses MorningBriefer, which wraps SPYDailyStrategy with:
      - VIX term structure context (yesterday's 08:55 snapshot)
      - Sector breadth context     (yesterday's 10:00 snapshot)
      - Today's high-impact events from event_calendar
      - Claude-synthesized narrative + skip + watch conditions

    The base play (regime + options) still comes from SPYDailyStrategy
    so its locked, backtested logic is unchanged. The briefer only adds
    context and decision hints.
    """
    logger.info("▶ Morning brief job starting")
    try:
        strategy = SPYDailyStrategy(
            polygon_client = polygon_client,
            vix_client     = vix_client,
            ivr_client     = ivr_client,
            event_calendar = event_calendar,
        )
        briefer = MorningBriefer(
            spy_strategy      = strategy,
            event_calendar    = event_calendar,
            earnings_calendar = EarningsCalendar(polygon_client=polygon_client),
            earnings_history  = EarningsHistory(polygon_client=polygon_client),
        )
        brief = briefer.build_today()

        # Plan is saved inside briefer; just post to Discord here.
        if post_fn:
            post_fn(brief["discord_message"])

        logger.info(
            f"Morning brief done — "
            f"regime={brief['regime']} | tradeable={brief['tradeable']} | "
            f"skip={len(brief.get('skip_conditions') or [])} | "
            f"watch={len(brief.get('watch_conditions') or [])}"
        )
    except Exception as e:
        logger.exception(f"Morning brief job failed: {e}")
        if post_fn:
            post_fn(f"⚠️ **Morning brief error:** {e}")


def job_spy_track_play(polygon_client, vix_client, ivr_client, post_fn,
                       track, event_calendar=None):
    """
    09:16 ET — Post an additional timeframe track's play (e.g. 5DTE) as its
    own alert. Shares the daily regime read but expresses it at the track's
    DTE + exit rules. Alert-only for now: it does NOT save a plan or paper-
    trade (the 45DTE morning brief owns the journal; per-track journaling is
    a deliberate follow-on). The owner trades the alert manually.
    """
    logger.info(f"▶ SPY track play [{track.name}]")
    try:
        strategy = SPYDailyStrategy(
            polygon_client = polygon_client,
            vix_client     = vix_client,
            ivr_client     = ivr_client,
            event_calendar = event_calendar,
        )
        card = strategy.build_today(track=track)
        if post_fn and card.get("discord_message"):
            post_fn(card["discord_message"])
        logger.info(f"Track [{track.name}] play posted — tradeable={card.get('tradeable')}")
    except Exception as e:
        logger.exception(f"SPY track play [{track.name}] failed: {e}")


def job_spy_close_snapshot(polygon_client, post_fn=None):
    """
    16:30 ET — Record SPY close price against today's plan.
    Lets you see tomorrow whether the regime call was confirmed.
    """
    logger.info("▶ SPY close snapshot")
    try:
        df = polygon_client.get_bars(
            "SPY",
            timeframe = config.SWING_PRIMARY_TIMEFRAME,
            limit     = 5,
            days_back = 5,
        )
        if df is None or len(df) == 0:
            logger.warning("Close snapshot: no data returned")
            return

        spy_close = float(df["close"].iloc[-1])
        today_str = date.today().isoformat()

        pl   = PlanLogger()
        plan = pl.get_plan(today_str)
        if plan:
            plan["spy_close_eod"] = spy_close
            pl.save_plan(plan)
            logger.info(f"SPY EOD close recorded: ${spy_close}")
        else:
            logger.info(f"No plan for {today_str} — close snapshot skipped")

    except Exception as e:
        logger.exception(f"SPY close snapshot failed: {e}")


# ─────────────────────────────────────────
# REGISTRATION — called from main.py
# ─────────────────────────────────────────

def register_spy_jobs(
    scheduler,           # the BackgroundScheduler already running in main.py
    polygon_client,
    vix_client,
    ivr_client,
    post_fn,             # post_message_sync from discord_bot.py
    event_calendar=None,
):
    """
    Register all three SPY daily jobs onto the existing scheduler.
    Call this from main.py after start_scheduler() returns.

    Example (add to main.py):
    ─────────────────────────
        from data.vix_client   import VIXClient
        from data.ivr_client   import IVRClient
        from alerts.discord_bot import post_message_sync
        from scheduler.spy_daily_scheduler import register_spy_jobs

        vix_client = VIXClient()
        ivr_client = IVRClient()

        scheduler = start_scheduler()   # already in main.py

        register_spy_jobs(
            scheduler      = scheduler,
            polygon_client = PolygonClient(),
            vix_client     = vix_client,
            ivr_client     = ivr_client,
            post_fn        = post_message_sync,
            event_calendar = [],   # populate with FOMC/CPI dates
        )
    ─────────────────────────
    """
    from apscheduler.triggers.cron import CronTrigger

    eastern = pytz.timezone("US/Eastern")

    # 09:15 ET — pre-market play
    scheduler.add_job(
        func    = job_spy_premarket,
        trigger = CronTrigger(
            day_of_week = "mon-fri",
            hour        = 9,
            minute      = 15,
            timezone    = eastern,
        ),
        kwargs  = {
            "polygon_client": polygon_client,
            "vix_client":     vix_client,
            "ivr_client":     ivr_client,
            "post_fn":        post_fn,
            "event_calendar": event_calendar,
        },
        id      = "spy_premarket",
        name    = "SPY Pre-Market Play",
        replace_existing = True,
    )

    # 09:16 ET — additional enabled daily tracks (e.g. 5DTE) as their own
    # alerts. 45DTE is the morning brief above; here we add the other
    # daily-backtestable, enabled tracks. Intraday tracks (0DTE/1DTE) are
    # skipped — they need the intraday engine, not yet built.
    from signals.timeframes import enabled_tracks
    extra = [t for t in enabled_tracks()
             if t.name != "45DTE" and t.daily_backtestable]
    for track in extra:
        scheduler.add_job(
            func    = job_spy_track_play,
            trigger = CronTrigger(
                day_of_week = "mon-fri", hour = 9, minute = 16, timezone = eastern,
            ),
            kwargs  = {
                "polygon_client": polygon_client,
                "vix_client":     vix_client,
                "ivr_client":     ivr_client,
                "post_fn":        post_fn,
                "track":          track,
                "event_calendar": event_calendar,
            },
            id      = f"spy_track_{track.name.lower()}",
            name    = f"SPY {track.name} Play",
            replace_existing = True,
        )

    # 16:30 ET — close snapshot
    scheduler.add_job(
        func    = job_spy_close_snapshot,
        trigger = CronTrigger(
            day_of_week = "mon-fri",
            hour        = 16,
            minute      = 30,
            timezone    = eastern,
        ),
        kwargs  = {
            "polygon_client": polygon_client,
            "post_fn":        post_fn,
        },
        id      = "spy_close_snapshot",
        name    = "SPY Close Snapshot",
        replace_existing = True,
    )

    logger.info("✅ SPY daily jobs registered:")
    logger.info("   09:15 ET — Pre-market play (45DTE)")
    for track in extra:
        logger.info(f"   09:16 ET — {track.name} play")
    logger.info("   16:30 ET — Close snapshot")
