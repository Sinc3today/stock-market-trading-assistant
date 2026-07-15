"""
scheduler/spy_daily_scheduler.py — SPY Daily Jobs for APScheduler

Adds three jobs to your existing APScheduler instance in main.py.
Do NOT run this as a standalone process — it integrates into main.py.

Jobs:
    09:15 ET  Pre-market  → Build daily play, log plan, post to Discord
    16:30 ET  Close snap  → Attach SPY close price to today's plan

Integration in main.py (see bottom of this file for exact lines):
    from scheduler.spy_daily_scheduler import register_spy_jobs
    register_spy_jobs(scheduler, polygon_client, ..., post_fn=notifier.message)

Posting uses the injected `post_fn(message: str)` — main.py wires this to
notifier.message (Pushover). It takes a plain string, not an alert dict.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date, datetime
from loguru import logger
import pytz

import config
from signals.spy_daily_strategy import SPYDailyStrategy
from signals.morning_briefer    import MorningBriefer
from journal.plan_logger         import PlanLogger
from data.earnings_calendar     import EarningsCalendar
from data.earnings_history      import EarningsHistory
from signals.regime_detector    import RegimeResult, Regime
from signals.options_layer      import OptionsLayer
from data.options_chain         import OptionsChain
from journal.trade_recorder     import TradeRecorder
from learning.shadow_tester     import run_shadow
from learning.dipbuy_forward     import maybe_open_dipbuy

ET = pytz.timezone("US/Eastern")


# ─────────────────────────────────────────
# SHADOW-TEST HELPERS
# ─────────────────────────────────────────

def _regime_and_levels_from_brief(brief: dict) -> "tuple[RegimeResult, float, float]":
    """Reconstruct a RegimeResult (plus spot and ivr floats) from a serialized
    morning-brief dict.

    The brief is produced by ``MorningBriefer.build_today()`` which in turn
    calls ``asdict(PlayCard)``. ``regime`` is stored as the enum *value*
    (e.g. ``"trending_up_calm"``), so we re-wrap it with ``Regime()``.

    Returns ``(regime_result, spot, ivr)`` — a pure data transform with no
    side-effects; easy to unit-test in isolation.
    """
    metrics = brief.get("metrics") or {}
    regime_result = RegimeResult(
        regime     = Regime(brief.get("regime", "unknown")),
        tradeable  = bool(brief.get("tradeable", False)),
        play       = brief.get("play", ""),
        confidence = float(brief.get("confidence") or 0.0),
        reasons    = list(brief.get("reasons") or []),
        metrics    = metrics,
    )
    spot = float(metrics.get("spy_close") or 0.0)
    ivr  = float(metrics.get("ivr") or 0.0)
    return regime_result, spot, ivr


def _run_daily_shadow(regime_result, *, spot: float, ivr: float) -> None:
    """Invoke the extension-gate shadow-test, fully isolated so a shadow
    failure can never disturb the real daily play (Standing Rule #10)."""
    try:
        run_shadow(
            regime_result,
            spot          = spot,
            ivr           = ivr,
            options_layer = OptionsLayer(options_chain=OptionsChain()),
            trade_recorder= TradeRecorder(),
        )
    except Exception as e:
        logger.warning(f"shadow-test failed (ignored): {e}")


def _run_daily_dipbuy(polygon_client, *, ivr: float) -> None:
    """Invoke the dip-buy forward paper-test, fully isolated (Standing Rule #10).
    Fetches SPY daily history for the RSI(14) trigger and records a candidate
    bull-debit on a fresh RSI<30 cross. Self-contained: spot = latest daily
    close (same value the morning brief uses). The OPEN is additionally gated
    to the entry window inside maybe_open_dipbuy."""
    for ticker in getattr(config, "DIPBUY_TICKERS", ["SPY"]):
        try:
            df = polygon_client.get_bars(
                ticker, timeframe=config.SWING_PRIMARY_TIMEFRAME, limit=80, days_back=130)
            if df is None or len(df) < 30:
                continue
            spot = float(df["close"].iloc[-1])
            maybe_open_dipbuy(
                df,
                spot          = spot,
                ivr           = ivr,
                options_layer = OptionsLayer(options_chain=OptionsChain()),
                recorder      = TradeRecorder(),
                ticker        = ticker,
            )
        except Exception as e:
            logger.warning(f"dipbuy forward-test failed for {ticker} (ignored): {e}")


# ─────────────────────────────────────────
# JOB FUNCTIONS
# ─────────────────────────────────────────

def _todays_call_push(brief: dict, play_fn) -> None:
    """One priority-1 push every trading morning: the play OR the stand-down and
    why (audit T1.5 — skip days used to be silent, so 'bot skipped' looked
    identical to 'bot broke')."""
    if not play_fn:
        return
    regime = str(brief.get("regime") or "?").replace("_", " ")
    if brief.get("tradeable"):
        strat = (brief.get("strategy") or (brief.get("options") or {}).get("strategy")
                 or "play")
        play_fn(title=f"📊 Today: {str(strat).replace('_', ' ')} ({regime})",
                body=(f"{brief.get('play') or ''}\n"
                      f"Entry window opens 09:45 ET — approve alert follows if it "
                      f"opens. Details: /today"))
    else:
        reasons = brief.get("skip_conditions") or brief.get("reasons") or []
        top = str(reasons[0])[:200] if reasons else "regime gates failed"
        play_fn(title=f"📊 Today: standing down ({regime})",
                body=f"No trade today. {top}\nDetails: /today")


def job_spy_premarket(
    polygon_client,
    vix_client,
    ivr_client,
    post_fn,              # post_fn(message: str) — main.py wires notifier.message
    event_calendar=None,
    play_fn=None,         # play_fn(title=, body=) — priority-1 daily call push
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
    if not config.is_trading_day(datetime.now(ET)):
        logger.info("spy_premarket: non-trading day, skipping")
        return
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

        # ── Extension-gate shadow-test ──────────────────────────────
        # Reconstruct RegimeResult from the serialized brief so run_shadow
        # can inspect regime/tradeable/play. brief["metrics"] carries the
        # real spy_close + ivr values computed by SPYDailyStrategy.
        _regime_result, _spot, _ivr = _regime_and_levels_from_brief(brief)
        _run_daily_shadow(_regime_result, spot=_spot, ivr=_ivr)

        # NOTE: the dip-buy OPEN moved to job_spy_entry at 09:45 ET so it never
        # fires pre-market (entry-window rule). The 09:15 brief is planning only.

        # Plan is saved inside briefer; just post to Discord here.
        if post_fn:
            post_fn(brief["discord_message"])

        # Daily call push — play or stand-down, never silent (T1.5).
        try:
            _todays_call_push(brief, play_fn)
        except Exception as e:
            logger.warning(f"today's-call push failed: {e}")

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


def job_spy_entry(polygon_client, ivr_client):
    """09:45 ET — open daily entries INSIDE the entry window. Split out from the
    09:15 brief so opens never fire pre-market (config.within_entry_window).
    Currently runs the dip-buy forward paper-test; the paper broker (45DTE) is
    scheduled separately in learning/scheduler at the same 09:45 slot."""
    logger.info("▶ SPY entry job (09:45) starting")
    if not config.is_trading_day(datetime.now(ET)):
        logger.info("spy_entry: non-trading day, skipping")
        return
    try:
        ivr = ivr_client.get_iv_rank("SPY")
    except Exception as e:
        logger.warning(f"spy_entry: ivr fetch failed ({e}); defaulting to 0.0")
        ivr = 0.0
    _run_daily_dipbuy(polygon_client, ivr=ivr)
    _run_qqq_condor_forward(polygon_client)
    _run_seven_dte_forward(polygon_client)


def _run_seven_dte_forward(polygon_client) -> None:
    """7DTE SPY condor PAPER candidate on condor-regime days (Standing Rule
    #10 — isolated). Best undeployed rung from the DTE-ladder study; must
    earn live promotion on the paper record (bar in the module docstring)."""
    try:
        from journal.plan_logger import PlanLogger
        from learning.seven_dte_forward import _today_et, maybe_open_seven_dte
        plan = PlanLogger().get_plan(_today_et().isoformat()) or {}
        if plan.get("strategy") != "iron_condor":
            return
        df = polygon_client.get_bars(
            "SPY", timeframe=config.SWING_PRIMARY_TIMEFRAME, limit=3, days_back=5)
        if df is None or not len(df):
            return
        spy_spot = float(df["close"].iloc[-1])
        from alerts.stop_watchdog import yf_spot
        vix = yf_spot("^VIX") or 16.0
        from journal.trade_recorder import TradeRecorder
        maybe_open_seven_dte(TradeRecorder(), spy_spot=spy_spot, vix=vix)
    except Exception as e:
        logger.warning(f"7DTE condor forward-test failed (ignored): {e}")


def _run_qqq_condor_forward(polygon_client) -> None:
    """QQQ condor PAPER candidate on condor-regime days (Standing Rule #10 —
    isolated). Regime comes from today's saved plan (the same call the SPY
    condor trades on); QQQ priced at VXN. Zero real capital — see the promotion
    bar in learning/qqq_condor_forward."""
    try:
        from datetime import date as _date
        from journal.plan_logger import PlanLogger
        plan = PlanLogger().get_plan(_date.today().isoformat()) or {}
        if plan.get("strategy") != "iron_condor":
            return
        df = polygon_client.get_bars(
            "QQQ", timeframe=config.SWING_PRIMARY_TIMEFRAME, limit=3, days_back=5)
        if df is None or not len(df):
            return
        qqq_spot = float(df["close"].iloc[-1])
        from alerts.stop_watchdog import yf_spot
        vxn = yf_spot("^VXN") or 20.0
        from learning.qqq_condor_forward import maybe_open_qqq_condor
        from journal.trade_recorder import TradeRecorder
        maybe_open_qqq_condor(TradeRecorder(), qqq_spot=qqq_spot, vxn=vxn)
    except Exception as e:
        logger.warning(f"qqq condor forward-test failed (ignored): {e}")


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
    if not config.is_trading_day(datetime.now(ET)):
        logger.info("spy_close_snapshot: non-trading day, skipping")
        return
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
    post_fn,             # post_fn(message: str) — main.py wires notifier.message
    event_calendar=None,
    play_fn=None,        # play_fn(title=, body=) — daily "today's call" push
):
    """
    Register all three SPY daily jobs onto the existing scheduler.
    Call this from main.py after start_scheduler() returns.

    Example (add to main.py):
    ─────────────────────────
        from data.vix_client   import VIXClient
        from data.ivr_client   import IVRClient
        from alerts.notifier   import Notifier
        from scheduler.spy_daily_scheduler import register_spy_jobs

        vix_client = VIXClient()
        ivr_client = IVRClient()
        notifier   = Notifier(pushover)

        scheduler = start_scheduler()   # already in main.py

        register_spy_jobs(
            scheduler      = scheduler,
            polygon_client = PolygonClient(),
            vix_client     = vix_client,
            ivr_client     = ivr_client,
            post_fn        = notifier.message,
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
            "play_fn":        play_fn,
        },
        id      = "spy_premarket",
        name    = "SPY Pre-Market Play",
        replace_existing = True,
    )

    # 09:45 ET — entry job: open daily entries inside the entry window
    scheduler.add_job(
        func    = job_spy_entry,
        trigger = CronTrigger(
            day_of_week = "mon-fri", hour = 9, minute = 45, timezone = eastern,
        ),
        kwargs  = {
            "polygon_client": polygon_client,
            "ivr_client":     ivr_client,
        },
        id      = "spy_entry",
        name    = "SPY Entry (dip-buy, entry window)",
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
    logger.info("   09:45 ET — Entry job (dip-buy, entry window)")
    for track in extra:
        logger.info(f"   09:16 ET — {track.name} play")
    logger.info("   16:30 ET — Close snapshot")
