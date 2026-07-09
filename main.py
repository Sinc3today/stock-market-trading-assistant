"""
main.py — Trading Assistant Entry Point
Starts the premarket scanner, swing scanner, and intraday scanner.

Run with:
    python main.py

The web dashboard (alerts, trades, journal, chats) is started automatically
as a uvicorn subprocess. See alerts/web_app.py.
"""

import subprocess
import sys
import time as time_module
from datetime import datetime, timedelta
from loguru import logger
import pytz
import os
import config
from data.polygon_client             import PolygonClient
from data.vix_client                 import VIXClient
from data.ivr_client                 import IVRClient
from data.event_calendar             import EventCalendar
from scheduler.spy_daily_scheduler   import register_spy_jobs
from learning.scheduler              import register_learning_jobs
from signals.macro_runner            import register_macro_jobs
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
from alerts.pushover_client     import PushoverClient
from alerts.notifier            import Notifier
from scanners.swing_scanner     import SwingScanner
from scanners.intraday_scanner  import IntradayScanner
from scanners.premarket         import PremarketScanner
from scanners.news_scanner      import NewsScanner
from scanners.economic_scanner  import EconomicScanner
from scanners.options_flow_scanner import OptionsFlowScanner

# ── Local scanner-status dict (replaces the Discord bot's shared dict) ────────
scanner_status: dict = {
    "running":   False,
    "last_scan": None,
}

# ── Notification router (Pushover-only) ───────────────────────────────────────
pushover = PushoverClient()
notifier = Notifier(pushover)

swing_scanner        = SwingScanner()
intraday_scanner     = IntradayScanner()
premarket_scanner    = PremarketScanner()
news_scanner         = NewsScanner()
economic_scanner     = EconomicScanner()
options_flow_scanner = OptionsFlowScanner()

# Wire notifier into all scanners
# .set_discord_fn -> now a silent log() wrapper (no push); kept for call-site compat
# .set_play_fn    -> actionable-play push path (disciplined opens only)
swing_scanner.set_discord_fn(notifier.alert)
intraday_scanner.set_discord_fn(notifier.alert)
intraday_scanner.set_play_fn(notifier.play)
intraday_scanner.set_approve_fn(notifier.approve)   # emergency approve on 1-3DTE opens (T-2026-07-09)
premarket_scanner.set_discord_fn(notifier.message)
options_flow_scanner.set_discord_fn(notifier.message)

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
                economic_scanner.post_economic_alert(release, notify_fn=notifier.message)
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


# Smart-stop watchdog state (trade copilot) — trade_ids alerted today; reset daily.
from alerts import stop_watchdog as _stop_wd
_stop_alerted: set = set()
_stop_alerted_date: list = [None]
_exit_alerted: set = set()
_exit_alerted_date: list = [None]
_spot_failures = _stop_wd.DataFailureTracker(threshold=3)
_watchdog_polygon_client = [None]


def _watchdog_polygon():
    """Reuse one PolygonClient across watchdog ticks (T4#17 — 78 fresh clients/
    day wasted connection setup against a rate-limited API)."""
    if _watchdog_polygon_client[0] is None:
        _watchdog_polygon_client[0] = PolygonClient()
    return _watchdog_polygon_client[0]


def run_live_exit_alerts():
    """Every 15 min during market hours: high-priority alert when a LIVE (real
    money) position hits the 70% profit target or the 21-DTE time exit — the
    paper book auto-closes there, but the user must close RH positions manually.
    Exit signals are never gated by the entry window."""
    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    if not config.is_trading_day(now):
        return
    from datetime import time as _time
    if not (_time(9, 30) <= now.time() <= _time(16, 0)):
        return
    if _exit_alerted_date[0] != now.date():               # daily reset
        _exit_alerted.clear(); _exit_alerted_date[0] = now.date()
    try:
        from journal.trade_recorder import TradeRecorder
        from alerts.live_exits import check_live_exits
        n = check_live_exits(TradeRecorder(), pushover, _exit_alerted)
        if n:
            logger.info(f"live_exits: {n} exit alert(s) fired")
    except Exception as e:
        logger.exception(f"live_exits failed: {e}")


def run_stop_watchdog():
    """Every 5 min during market hours: emergency-alert if SPY nears an open
    position's short strike — the smart stop RH can't do. Keyed off the
    underlying, not the option mark. EXITS are never gated by the entry window."""
    if not getattr(config, "STOP_WATCHDOG_ENABLED", True):
        return
    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    if not config.is_trading_day(now):
        return
    from datetime import time as _time
    if not (_time(9, 30) <= now.time() <= _time(16, 0)):   # market hours only
        return
    if _stop_alerted_date[0] != now.date():                # daily reset
        _stop_alerted.clear(); _stop_alerted_date[0] = now.date()
    try:
        from journal.trade_recorder import TradeRecorder

        def _polygon_spot():
            df = _watchdog_polygon().get_bars("SPY", config.SWING_PRIMARY_TIMEFRAME,
                                              limit=1, days_back=3)
            return float(df["close"].iloc[-1]) if df is not None and len(df) else None

        # Polygon first, yfinance fallback; escalate loudly if BOTH are down —
        # a silent data failure means stop coverage is off (audit T1.4).
        spot = _stop_wd.resolve_spot(_polygon_spot, _stop_wd.yf_spot)
        if spot is None:
            if _spot_failures.record_failure(today=now.date()) and pushover:
                pushover.send("⚠️ Stop coverage degraded",
                              "Can't fetch SPY from Polygon OR yfinance — the stop "
                              "watchdog is blind. Watch your positions manually.",
                              priority=1)
            return
        _spot_failures.record_success()
        n = _stop_wd.check_open_positions(TradeRecorder(), spot, pushover,
                                          _stop_alerted, config.STOP_WATCHDOG_BUFFER_PCT)
        if n:
            logger.info(f"stop_watchdog: {n} stop alert(s) fired at SPY ${spot:.2f}")
    except Exception as e:
        logger.exception(f"stop_watchdog failed: {e}")


def run_premarket_gap_check():
    """09:15 ET: if SPY gapped overnight to at/near any open short strike, alert
    BEFORE the bell instead of discovering it at the first 9:30 watchdog tick
    (audit T1.4 — weekend/overnight gaps were unmonitored)."""
    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    if not config.is_trading_day(now):
        return
    try:
        from journal.trade_recorder import TradeRecorder
        spot = _stop_wd.yf_spot()          # yfinance carries pre-market context
        if spot is None:
            return
        n = _stop_wd.check_open_positions(TradeRecorder(), spot, pushover,
                                          _stop_alerted, config.STOP_WATCHDOG_BUFFER_PCT)
        if n:
            logger.warning(f"premarket gap check: {n} alert(s) at SPY ${spot:.2f}")
    except Exception as e:
        logger.exception(f"premarket gap check failed: {e}")


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
    scheduler = BackgroundScheduler(
        timezone=eastern,
        # T4#16: a hung/slow job must not cascade — late fires collapse into one
        # (coalesce) and anything >10 min late is dropped rather than queued.
        job_defaults={"coalesce": True, "misfire_grace_time": 600, "max_instances": 1},
    )

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

    # Intraday — every 5 minutes, starting at next 9:30 AM ET
    now_et    = datetime.now(eastern)
    next_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et >= next_open:
        next_open = next_open + timedelta(days=1)

    scheduler.add_job(
        run_intraday_scan,
        IntervalTrigger(
            minutes    = config.INTRADAY_SCAN_INTERVAL_MIN,
            start_date = next_open,
            timezone   = eastern,
        ),
        id   = "intraday_scan",
        name = "Intraday Scanner",
    )

    # Smart-stop watchdog — every 5 min, market hours (self-gated). Emergency
    # alert when SPY nears an open position's short strike (trade copilot).
    scheduler.add_job(
        run_stop_watchdog,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5", timezone=eastern),
        id="stop_watchdog",
        name="Smart-stop watchdog",
    )

    # Live-book exit alerts — every 15 min, market hours (self-gated). High-
    # priority push when a real position hits the profit target / time exit.
    scheduler.add_job(
        run_live_exit_alerts,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="7,22,37,52", timezone=eastern),
        id="live_exit_alerts",
        name="Live-book exit alerts",
    )

    # Pre-market gap check — 09:15 ET. Overnight/weekend gaps to a short strike
    # alert before the bell, not at the first 9:30 watchdog tick.
    scheduler.add_job(
        run_premarket_gap_check,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=15, timezone=eastern),
        id="premarket_gap_check",
        name="Pre-market gap check",
    )

    # Options flow scan — 9:35 AM ET weekdays
    scheduler.add_job(
        lambda: options_flow_scanner.run(),
        CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=eastern),
        id   = "options_flow_scan",
        name = "Options Flow Scanner",
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

    # Liveness watchdog — ping systemd's WatchdogSec + stamp a heartbeat every
    # 60s. If the bot freezes the pings stop and systemd restarts it; on startup
    # an emergency Pushover fires if we were down (2026-06-15 silent-freeze fix).
    from alerts import watchdog
    watchdog.check_recovery(pushover)   # alert if there was a gap before this start
    watchdog.ping()                     # immediate first ping + fresh stamp
    scheduler.add_job(
        watchdog.ping,
        IntervalTrigger(seconds=60, timezone=eastern),
        id="watchdog_ping",
        name="Liveness watchdog",
    )

    scheduler.start()
    logger.info("✅ Scheduler started")
    logger.info("   Liveness watchdog: ping every 60s (systemd auto-restart + recovery alert)")
    logger.info("   Morning briefing: weekdays at 7:45 AM EST")
    logger.info("   Premarket scan:   weekdays at 8:00 AM EST")
    logger.info("   Swing scan:       weekdays at 9:00 AM EST")
    logger.info(f"  Intraday scan:    every {config.INTRADAY_SCAN_INTERVAL_MIN} minutes")
    logger.info("   Options flow scan: weekdays at 9:35 AM EST")
    logger.info("   Midday briefing:  weekdays at 12:00 PM EST")
    logger.info("   EOD briefing:     weekdays at 3:45 PM EST")
    logger.info("   Economic scan:    weekdays hourly 8:30 AM - 4:30 PM EST")
    return scheduler


# ─────────────────────────────────────────
# CHILD-PROCESS LIFETIME (Windows)
# ─────────────────────────────────────────
#
# Subprocesses on Windows survive their parent on a hard kill (Stop-Process,
# taskkill /F, crash). Bind the uvicorn child to a Job Object with
# JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so the kernel kills it whenever this
# process exits, by any path. No-op on non-Windows platforms.
_job_handles: list = []  # keep job handles alive for the parent's lifetime


def _bind_to_job_object(proc: subprocess.Popen) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation  = 9

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount",  ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount",   ctypes.c_ulonglong),
            ("WriteTransferCount",  ctypes.c_ulonglong),
            ("OtherTransferCount",  ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit",     ctypes.c_int64),
            ("LimitFlags",              wintypes.DWORD),
            ("MinimumWorkingSetSize",   ctypes.c_size_t),
            ("MaximumWorkingSetSize",   ctypes.c_size_t),
            ("ActiveProcessLimit",      wintypes.DWORD),
            ("Affinity",                ctypes.c_size_t),
            ("PriorityClass",           wintypes.DWORD),
            ("SchedulingClass",         wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo",                IO_COUNTERS),
            ("ProcessMemoryLimit",    ctypes.c_size_t),
            ("JobMemoryLimit",        ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed",     ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        logger.warning(f"CreateJobObjectW failed: {ctypes.get_last_error()}")
        return

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    if not kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        logger.warning(f"SetInformationJobObject failed: {ctypes.get_last_error()}")
        return

    if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
        logger.warning(f"AssignProcessToJobObject failed: {ctypes.get_last_error()}")
        return

    # Hold the job handle for the lifetime of this process so its handle
    # close (when we exit) is what triggers the kernel to kill the child.
    _job_handles.append(job)


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────

if __name__ == "__main__":
    from runtime.singleton import acquire_or_die, SingletonLockError
    try:
        acquire_or_die(os.path.join(config.LOG_DIR, "main.lock"))
    except SingletonLockError as e:
        logger.error(f"Refusing to start: {e}")
        sys.exit(1)

    logger.info("=" * 52)
    logger.info("   Trading Assistant Starting Up")
    logger.info("=" * 52)
    logger.info(f"Environment:   {config.ENVIRONMENT}")
    logger.info(f"Polygon key:   {'✅ Set' if config.POLYGON_API_KEY   else '❌ Missing'}")
    logger.info(f"Alpaca key:    {'✅ Set' if config.ALPACA_API_KEY    else '❌ Missing'}")
    logger.info(f"Pushover:      {'✅ Set' if config.PUSHOVER_API_TOKEN else '❌ Missing'}")
    logger.info(f"Detail page:   {config.PUSHOVER_BASE_URL or '⚠️  PUSHOVER_BASE_URL not set — links disabled'}")
    logger.info("-" * 52)

    # Start scheduler
    scheduler = start_scheduler()

    # Wire SPY daily strategy jobs
    try:
        event_cal  = EventCalendar()
        vix_client = VIXClient()
        ivr_client = IVRClient(
            polygon_client = PolygonClient(),
            vix_client     = vix_client,
        )
        register_spy_jobs(
            scheduler      = scheduler,
            polygon_client = PolygonClient(),
            vix_client     = vix_client,
            ivr_client     = ivr_client,
            post_fn        = notifier.message,
            event_calendar = event_cal,   # pass the EventCalendar object, not its block-date list
            play_fn        = notifier.play,   # daily "today's call" push (T1.5)
        )
        logger.info("✅ SPY daily strategy jobs registered")
        logger.info("   09:15 ET -- Pre-market play")
        logger.info("   16:30 ET -- Close snapshot")
    except Exception as e:
        logger.error(f"SPY daily jobs failed to register: {e}")

    # Self-learning loop: paper trades, EOD scoring, Claude reflection, weekly hypothesis
    try:
        register_learning_jobs(
            scheduler      = scheduler,
            polygon_client = PolygonClient(),
            vix_client     = VIXClient(),   # exit_manager marks spreads with VIX as IV
            post_fn=notifier.message,
            play_fn=notifier.play,
            approve_fn=notifier.approve,   # emergency entry-approve (one-tap to /copilot)
        )
        logger.info("✅ Self-learning jobs registered")
    except Exception as e:
        logger.error(f"Self-learning jobs failed to register: {e}")

    # Macro daily snapshots: VIX term structure + sector breadth
    try:
        register_macro_jobs(
            scheduler      = scheduler,
            polygon_client = PolygonClient(),
            post_fn        = notifier.message,
        )
        logger.info("✅ Macro snapshot jobs registered")
    except Exception as e:
        logger.error(f"Macro snapshot jobs failed to register: {e}")
    # Start alert web app (subprocess so uvicorn owns its own event loop)
    logger.info(
        f"Starting alert web app on "
        f"http://{config.WEB_SERVER_HOST}:{config.WEB_SERVER_PORT}"
    )
    web_app_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "alerts.web_app:app",
         "--host", str(config.WEB_SERVER_HOST),
         "--port", str(config.WEB_SERVER_PORT),
         "--log-level", "warning"],
        cwd = os.path.dirname(os.path.abspath(__file__)),
    )
    _bind_to_job_object(web_app_process)
    logger.info(f"   Alert web app: running on port {config.WEB_SERVER_PORT}")

    logger.info("-" * 52)
    logger.info("✅ All systems running")
    logger.info("   Scanners:  scheduled and waiting")
    logger.info("   Pushover:  active (play events only)")
    logger.info(f"  Web app:   http://localhost:{config.WEB_SERVER_PORT}/")
    logger.info("   Stop:      Ctrl+C")
    logger.info("=" * 52)

    # Keep main thread alive + hourly heartbeat
    try:
        while True:
            time_module.sleep(60)
            now = datetime.now()
            if now.minute == 0:
                logger.info(f"💓 Heartbeat — {now.strftime('%I:%M %p')}")
    except KeyboardInterrupt:
        logger.info("Shutting down Trading Assistant...")
        try:
            web_app_process.terminate()
        except Exception:
            pass
        scheduler.shutdown()
        logger.info("Goodbye.")
