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

swing_scanner    = SwingScanner()
intraday_scanner = IntradayScanner()
premarket_scanner = PremarketScanner()

# Inject Discord posting function into all scanners
swing_scanner.set_discord_fn(post_alert_sync)
intraday_scanner.set_discord_fn(post_alert_sync)
premarket_scanner.set_discord_fn(post_alert_sync)

# Wire premarket priority list into swing scanner
swing_scanner.premarket_scanner = premarket_scanner


# ─────────────────────────────────────────
# SCANNER JOBS
# ─────────────────────────────────────────

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

    scheduler.start()
    logger.info("✅ Scheduler started")
    logger.info("   Premarket scan: weekdays at 8:00 AM EST")
    logger.info("   Swing scan:     weekdays at 9:00 AM EST")
    logger.info(f"  Intraday scan:  every {config.INTRADAY_SCAN_INTERVAL_MIN} minutes")
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
            time_module.sleep(1)
            now = datetime.now()
            if now.second == 0 and now.minute == 0:
                logger.info(
                    f"💓 Heartbeat — {now.strftime('%I:%M %p')} | "
                    f"Alerts today: {scanner_status['alerts_today']}"
                )
    except KeyboardInterrupt:
        logger.info("Shutting down Trading Assistant...")
        scheduler.shutdown()
        logger.info("Goodbye.")