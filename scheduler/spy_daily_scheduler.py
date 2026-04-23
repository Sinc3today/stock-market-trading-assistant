"""
scheduler/spy_daily_scheduler.py — SPY Daily Jobs for APScheduler

Adds three jobs to your existing APScheduler instance in main.py.
Do NOT run this as a standalone process — it integrates into main.py.

Jobs:
    09:15 ET  Pre-market  → Build daily play, log plan, post to Discord
    16:30 ET  Close snap  → Attach SPY close price to today's plan
    19:00 ET  Reflection  → Post your evening review prompt to Discord

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

from datetime import datetime, date
from loguru import logger
import pytz

import config
from signals.spy_daily_strategy import SPYDailyStrategy
from journal.plan_logger         import PlanLogger

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
    09:15 ET — Build daily SPY play and post to Discord.
    Skips automatically on weekends (APScheduler cron handles this).
    """
    logger.info("▶ SPY pre-market job starting")
    try:
        strategy = SPYDailyStrategy(
            polygon_client = polygon_client,
            vix_client     = vix_client,
            ivr_client     = ivr_client,
            event_calendar = event_calendar,
        )
        play = strategy.build_today()

        # Save to plan log regardless of tradeable/skip
        PlanLogger().save_plan(play.get("plan_payload", {}))

        # Post to Discord
        if post_fn:
            post_fn(play["discord_message"])

        logger.info(
            f"SPY pre-market done — "
            f"regime={play['regime']} | tradeable={play['tradeable']}"
        )
    except Exception as e:
        logger.exception(f"SPY pre-market job failed: {e}")
        if post_fn:
            post_fn(f"⚠️ **SPY daily play error:** {e}")


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


def job_spy_reflection(post_fn):
    """
    19:00 ET — Post evening reflection prompt to Discord.
    5 fixed questions. Answer them in your journal, not in Discord.
    """
    logger.info("▶ SPY reflection ping")
    try:
        if post_fn:
            post_fn(_reflection_message())
    except Exception as e:
        logger.exception(f"SPY reflection job failed: {e}")


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

    # 19:00 ET — reflection prompt
    scheduler.add_job(
        func    = job_spy_reflection,
        trigger = CronTrigger(
            day_of_week = "mon-fri",
            hour        = 19,
            minute      = 0,
            timezone    = eastern,
        ),
        kwargs  = {"post_fn": post_fn},
        id      = "spy_reflection",
        name    = "SPY Evening Reflection",
        replace_existing = True,
    )

    logger.info("✅ SPY daily jobs registered:")
    logger.info("   09:15 ET — Pre-market play")
    logger.info("   16:30 ET — Close snapshot")
    logger.info("   19:00 ET — Reflection prompt")


# ─────────────────────────────────────────
# REFLECTION MESSAGE
# ─────────────────────────────────────────

def _reflection_message() -> str:
    today = date.today().isoformat()
    return (
        f"🪞 **SPY Daily Reflection — {today}**\n"
        f"_5 minutes. No skipping._\n\n"
        f"**1.** What was today's regime call? Did price confirm it by close?\n"
        f"**2.** Did you execute the plan or improvise? Why?\n"
        f"**3.** If you traded: was P&L from edge, luck, or a vol move?\n"
        f"**4.** If you skipped: was the skip justified? Any FOMO?\n"
        f"**5.** What's your bias going into tomorrow's open?\n\n"
        f"_Log your answers in your journal. Patterns emerge over 30+ days._"
    )
