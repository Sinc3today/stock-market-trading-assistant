"""
main.py — Trading Assistant Entry Point
Starts the Discord bot, premarket scanner, swing scanner, and intraday scanner.

Run with:
    python main.py

Dashboard runs separately:
    python -m streamlit run alerts/dashboard.py
"""

import sys
import threading
import time as time_module
from datetime import datetime
from loguru import logger
import pytz
import os
import config
from alerts.discord_bot           import post_message_sync
from scheduler.spy_daily_scheduler import register_spy_jobs
from data.vix_client              import VIXClient
from data.ivr_client              import IVRClient
from alerts.discord_bot           import post_message_sync
from scheduler.spy_daily_scheduler import register_spy_jobs
# ── Logging setup ────────────────────────────────────────────
os.makedirs(config.LOG_DIR, exist_ok=True)

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level=config.LOG_LEVEL,
    colorize=True,
)
logger.add(
    f"{config.LOG_DIR}app.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG",
)

# ── Shared state ─────────────────────────────────────────────
from alerts.discord_bot import scanner_status, post_alert_sync
from scanners.swing_scanner import SwingScanner
from scanners.intraday_scanner import IntradayScanner
from scanners.premarket import PremarketScanner
from scanners.news_scanner import NewsScanner
from scanners.economic_scanner import EconomicScanner

swing_scanner    = SwingScanner()
intraday_scanner = IntradayScanner()
premarket_scanner = PremarketScanner()
news_scanner       = NewsScanner()
economic_scanner   = EconomicScanner()

# Inject Discord posting function into all scanners
swing_scanner.set_discord_fn(post_alert_sync)
intraday_scanner.set_discord_fn(post_alert_sync)
premarket_scanner.set_discord_fn(post_alert_sync)

# Wire premarket priority list into swing scanner
swing_scanner.premarket_scanner = premarket_scanner


# ─────────────────────────────────────────
# SCANNER JOBS
# ─────────────────────────────────────────

def run_morning_briefing():
    """Called at 7:45 AM EST — before premarket scan."""
    logger.info("📰 Morning briefing starting...")
    try:
        news_scanner.run(briefing_type="morning")
    except Exception as e:
        logger.error(f"Morning briefing failed: {e}")


def run_economic_scan():
    """Called hourly during market hours — checks for new releases."""
    logger.info("📊 Economic scan starting...")
    try:
        releases = economic_scanner.scan_for_new_releases(days_back=1)
        for release in releases:
            if release.get("is_high_impact") and release.get("discord_alert"):
                economic_scanner.post_economic_alert(release)
                logger.info(f"Economic alert posted: {release['name']}")
        if releases:
            logger.info(f"Economic scan: {len(releases)} new release(s) found")
        else:
            logger.debug("Economic scan: no new releases")
    except Exception as e:
        logger.error(f"Economic scan failed: {e}")


def run_midday_briefing():
    """Called at 12:00 PM EST."""
    logger.info("📰 Midday briefing starting...")
    try:
        news_scanner.run(briefing_type="midday")
    except Exception as e:
        logger.error(f"Midday briefing failed: {e}")


def run_eod_briefing():
    """Called at 3:45 PM EST."""
    logger.info("📰 End of day briefing starting...")
    try:
        news_scanner.run(briefing_type="eod")
    except Exception as e:
        logger.error(f"EOD briefing failed: {e}")


def run_premarket_scan():
    """Called at 8:00 AM EST — 90 min before open."""
    logger.info("🌅 Scheduled premarket scan starting...")
    scanner_status["running"]   = True
    scanner_status["last_scan"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    try:
        results = premarket_scanner.run()
        logger.info(f"Premarket scan done — {len(results)} setups found")
    except Exception as e:
        logger.error(f"Premarket scan failed: {e}")
    finally:
        scanner_status["running"] = False


def run_swing_scan():
    """Called at 9:00 AM EST — market open."""
    logger.info("⏰ Scheduled swing scan starting...")
    scanner_status["running"]   = True
    scanner_status["last_scan"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    try:
        alerts = swing_scanner.run()
        logger.info(f"Swing scan done — {len(alerts)} alerts fired")
    except Exception as e:
        logger.error(f"Swing scan failed: {e}")
    finally:
        scanner_status["running"] = False


def run_intraday_scan():
    """Called every 5 minutes during market hours."""
    scanner_status["last_scan"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    try:
        alerts = intraday_scanner.run()
        if alerts:
            logger.info(f"Intraday scan — {len(alerts)} alerts fired")
    except Exception as e:
        logger.error(f"Intraday scan failed: {e}")


# ─────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────

def start_scheduler():
    """
    Premarket scan:  8:00 AM EST weekdays
    Swing scan:      9:00 AM EST weekdays
    Intraday scan:   every 5 min
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    eastern = pytz.timezone("US/Eastern")
    scheduler = BackgroundScheduler(timezone=eastern)

    # Morning briefing — 7:45 AM EST weekdays
    scheduler.add_job(
        run_morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=45, timezone=eastern),
        id="morning_briefing",
        name="Morning News Briefing",
    )

    # Premarket — 8:00 AM EST weekdays
    scheduler.add_job(
        run_premarket_scan,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=eastern),
        id="premarket_scan",
        name="Premarket Scanner",
    )

    # Swing — 9:00 AM EST weekdays
    scheduler.add_job(
        run_swing_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=eastern),
        id="swing_scan",
        name="Swing Scanner",
    )

    # Intraday — every 5 minutes
    scheduler.add_job(
        run_intraday_scan,
        IntervalTrigger(minutes=config.INTRADAY_SCAN_INTERVAL_MIN),
        id="intraday_scan",
        name="Intraday Scanner",
    )

    # Midday briefing — 12:00 PM EST weekdays
    scheduler.add_job(
        run_midday_briefing,
        CronTrigger(day_of_week="mon-fri", hour=12, minute=0, timezone=eastern),
        id="midday_briefing",
        name="Midday News Briefing",
    )

    # End of day briefing — 3:45 PM EST weekdays
    scheduler.add_job(
        run_eod_briefing,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=eastern),
        id="eod_briefing",
        name="EOD News Briefing",
    )

    # Economic scan — every hour during market hours weekdays
    scheduler.add_job(
        run_economic_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour="8-16",
            minute=30,
            timezone=eastern
        ),
        id="economic_scan",
        name="Economic Data Scanner",
    )

    scheduler.start()
    logger.info("✅ Scheduler started")
    logger.info("   Morning briefing: weekdays at 7:45 AM EST")
    logger.info("   Premarket scan:   weekdays at 8:00 AM EST")
    logger.info("   Swing scan:       weekdays at 9:00 AM EST")
    logger.info(f"  Intraday scan:    every {config.INTRADAY_SCAN_INTERVAL_MIN} minutes")
    logger.info("   Midday briefing:  weekdays at 12:00 PM EST")
    logger.info("   EOD briefing:     weekdays at 3:45 PM EST")
    logger.info("   Economic scan:    weekdays hourly 8:30 AM - 4:30 PM EST")
    return scheduler


# ─────────────────────────────────────────
# DISCORD BOT
# ─────────────────────────────────────────

def start_discord():
    from alerts.discord_bot import run_bot
    logger.info("Starting Discord bot thread...")
    run_bot()


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=" * 52)
    logger.info("   Trading Assistant Starting Up")
    logger.info("=" * 52)
    logger.info(f"Environment:   {config.ENVIRONMENT}")
    logger.info(f"Polygon key:   {'✅ Set' if config.POLYGON_API_KEY   else '❌ Missing'}")
    logger.info(f"Discord token: {'✅ Set' if config.DISCORD_BOT_TOKEN else '❌ Missing'}")
    logger.info(f"Alpaca key:    {'✅ Set' if config.ALPACA_API_KEY    else '❌ Missing'}")
    logger.info("-" * 52)

    # Start scheduler
    scheduler = start_scheduler()

    register_spy_jobs(
        scheduler      = scheduler,
        polygon_client = PolygonClient(),  # already imported at top
        vix_client     = None,             # swap in VIXClient() when built
        ivr_client     = None,             # swap in IVRClient() when built
        post_fn        = post_message_sync,
        event_calendar = [],
    )
    # Start Discord bot in background thread
    discord_thread = threading.Thread(target=start_discord, daemon=True)
    discord_thread.start()

    logger.info("-" * 52)
    logger.info("✅ All systems running")
    logger.info("   Scanners:  scheduled and waiting")
    logger.info("   Discord:   online")
    logger.info("   Dashboard: python -m streamlit run alerts/dashboard.py")
    logger.info("   Stop:      Ctrl+C")
    logger.info("=" * 52)

    # Keep main thread alive + hourly heartbeat
    try:
        while True:
            time_module.sleep(60)
            now = datetime.now()
            if now.minute == 0:
                logger.info(
                    f"💓 Heartbeat — {now.strftime('%I:%M %p')} | "
                    f"Alerts today: {scanner_status['alerts_today']}"
                )
    except KeyboardInterrupt:
        logger.info("Shutting down Trading Assistant...")
        scheduler.shutdown()
        logger.info("Goodbye.")
