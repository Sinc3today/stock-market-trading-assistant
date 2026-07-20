"""
learning/scheduler.py -- Register self-learning jobs onto the main APScheduler.

Call register_learning_jobs(scheduler, polygon_client, post_fn) from main.py
*after* the SPY daily jobs are already registered. This adds:

    09:16 ET (Mon-Fri)  paper_broker.execute_today()
    16:05 ET (Mon-Fri)  outcome_resolver.resolve_today()
    16:08 ET (Mon-Fri)  exit_manager.manage_open()    (profit target / time stop)
    16:10 ET (Mon-Fri)  expiry_resolver.resolve_expired()
    19:01 ET (Mon-Fri)  reflector.reflect_today()
    Sat 10:00 ET        hypothesis_engine.propose_weekly()
    Sat 11:00 ET        hypothesis_runner.run_pending()
    Sun 10:00 ET        off_hours_learner.run()

Every job is wrapped in try/except so one failure can never crash the bot.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from datetime import datetime
from loguru import logger

import config

from learning.paper_broker      import PaperBroker
from learning.outcome_resolver  import OutcomeResolver, format_resolved_message
from learning.predictions       import PredictionLog
from learning.reflector         import Reflector
from learning.hypothesis_engine import HypothesisEngine
from learning.hypothesis_runner import HypothesisRunner
from learning.off_hours_learner import OffHoursLearner
from learning.expiry_resolver   import ExpiryResolver, format_expiry_message
from learning.exit_manager      import (
    ExitManager, format_exit_message, format_exit_digest_title,
)
from journal.trade_recorder     import TradeRecorder


# ── JOB WRAPPERS ──────────────────────────────────────

def job_paper_broker(play_fn=None, approve_fn=None):
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("paper_broker: non-trading day, skipping")
        return
    try:
        result = PaperBroker().execute_today()
        logger.info(f"learning.paper_broker -> {result}")
        if not result.get("recorded"):
            return
        trade_id = result.get("trade_id")
        # Prefer the emergency entry-approve alert (RH-shaped legs + one-tap to
        # /copilot) over the plain priority-1 buzz — this is the "approve it"
        # alert the user asked for so they don't miss the window.
        if approve_fn and trade_id:
            trade = next((t for t in TradeRecorder().get_open_trades()
                          if t.get("trade_id") == trade_id), None)
            if trade:
                try:
                    approve_fn(trade)
                    return
                except Exception as e:
                    logger.warning(f"paper_broker approve notify failed: {e}")
        if play_fn:
            try:
                play_fn(title="📈 Daily play opened",
                        body=f"45DTE disciplined play opened — {trade_id}")
            except Exception as e:
                logger.warning(f"paper_broker play notify failed: {e}")
    except Exception as e:
        logger.exception(f"learning.paper_broker failed: {e}")


_rh_expiry_pushed: list = [None]   # date of the last expired-session push


def job_rh_sync(alert_fn=None):
    """Poll Robinhood READ-ONLY and reconcile open positions into the live book
    so the copilot/watchdog track real trades hands-off. Self-gated to trading
    days; on an expired session it pushes ONE nudge per day (T1.3 — the old
    silent warning left sync dead for days) and never crashes the bot."""
    today = datetime.now(pytz.timezone("US/Eastern")).date()
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        return
    try:
        from learning.rh_sync import sync
        plan = sync(dry_run=False)
        created = sum(1 for s in plan if s.get("action") == "create")
        closed = sum(1 for s in plan if s.get("action") == "close")
        if created or closed:
            logger.info(f"rh_sync: {created} new / {closed} closed-on-RH position(s)")
            if closed and alert_fn:
                alert_fn(title="✅ RH position closed",
                         body=f"{closed} position(s) no longer open on Robinhood — "
                              f"marked closed in the journal (exit at current mid; "
                              f"correct on /copilot if the fill differed).")
    except RuntimeError as e:
        logger.warning(f"rh_sync skipped: {e}")
        if alert_fn and _rh_expiry_pushed[0] != today:
            _rh_expiry_pushed[0] = today
            alert_fn(title="⚠️ RH sync is down",
                     body="Robinhood session expired — position sync and close-"
                          "detection are blind until you re-run:\n"
                          "cd ~/Projects/stock-market-trading-assistant && "
                          ".venv/bin/python -m learning.rh_sync login")
    except Exception as e:
        logger.exception(f"rh_sync failed: {e}")


def job_loop_health(alert_fn=None):
    """Daily: flag any silently-stale learning artifact (off-hours output,
    predictions, KB growth, spy_history.csv, RH session) so a broken component
    surfaces in days, not the ~5 weeks the off-hours learner sat dead."""
    try:
        from learning.loop_health import gather_and_assess
        issues = gather_and_assess()
        if issues:
            logger.warning("loop_health: " + " | ".join(issues))
            if alert_fn:
                alert_fn(title="⚠️ Learning loop needs attention",
                         body="\n".join(f"• {i}" for i in issues))
        else:
            logger.info("loop_health: all learning artifacts fresh")
    except Exception as e:
        logger.exception(f"loop_health failed: {e}")


def job_refresh_csv():
    """Weekly: keep backtests/spy_history.csv current so the off-hours replay
    (and backtests) never run on stale data."""
    try:
        from learning.loop_health import refresh_spy_history
        n = refresh_spy_history()
        logger.info(f"refresh_csv: appended {n} new SPY row(s)")
    except Exception as e:
        logger.exception(f"refresh_csv failed: {e}")


def job_outcome_resolver(polygon_client, post_fn=None):
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("outcome_resolver: non-trading day, skipping")
        return
    try:
        result = OutcomeResolver(polygon_client=polygon_client).resolve_today()
        logger.info(f"learning.outcome_resolver -> {result}")
        if result.get("resolved") and post_fn:
            prediction = PredictionLog().get(result["date"])
            if prediction:
                try:
                    post_fn(format_resolved_message(prediction))
                except Exception as e:
                    logger.warning(f"learning.outcome_resolver notify failed: {e}")
    except Exception as e:
        logger.exception(f"learning.outcome_resolver failed: {e}")


def job_exit_manager(polygon_client, vix_client=None, post_fn=None,
                     play_fn=None, dte_buckets=None):
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("exit_manager: non-trading day, skipping")
        return
    try:
        closed = ExitManager(
            polygon_client=polygon_client, vix_client=vix_client,
        ).manage_open(dte_buckets=dte_buckets)
        logger.info(f"learning.exit_manager [{dte_buckets or 'all'}] -> {len(closed)} closed")
        # No per-run push: exits are consolidated into the EOD digest
        # (job_exit_digest) so the phone gets one disciplined-only summary
        # instead of a buzz per 5-min closure.
    except Exception as e:
        logger.exception(f"learning.exit_manager failed: {e}")


def job_expiry_resolver(polygon_client, post_fn=None, play_fn=None):
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("expiry_resolver: non-trading day, skipping")
        return
    try:
        closed = ExpiryResolver(polygon_client=polygon_client).resolve_expired()
        logger.info(f"learning.expiry_resolver -> {len(closed)} closed")
        # No per-run push: folded into the EOD digest (job_exit_digest).
    except Exception as e:
        logger.exception(f"learning.expiry_resolver failed: {e}")


def job_exit_digest(play_fn=None):
    """End-of-day consolidated exit notification.

    Reads today's DISCIPLINED closures from the journal (covering intraday,
    45DTE, and expiry exits alike) and sends ONE push with a net-P&L summary.
    Also appends a clearly-labeled CANDIDATE (dip-buy forward-test) section when
    candidate trades closed today. Learning-book exits stay silent. No closures
    of either kind -> no push.
    """
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("exit_digest: non-trading day, skipping")
        return
    try:
        today    = datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")
        closed_today = [t for t in TradeRecorder().get_closed_trades()
                        if (t.get("exit_date") or "").startswith(today)]
        disc = [t for t in closed_today if t.get("book") == "disciplined"]
        cand = [t for t in closed_today if t.get("book") == "candidate"]
        logger.info(f"learning.exit_digest -> {len(disc)} disciplined, "
                    f"{len(cand)} candidate closes today")
        if not (disc or cand) or not play_fn:
            return
        sections = []
        if disc:
            sections.append(format_exit_message(disc))
        if cand:
            sections.append("**📕 Forward-test (candidate) — not real money**\n"
                            + format_exit_message(cand))
        title = (format_exit_digest_title(disc) if disc
                 else f"📕 Forward-test exits: {len(cand)} closed")
        try:
            play_fn(title=title, body="\n\n".join(sections))
        except Exception as e:
            logger.warning(f"exit_digest play notify failed: {e}")
    except Exception as e:
        logger.exception(f"learning.exit_digest failed: {e}")


def job_dipbuy_resolver(polygon_client, vix_client=None):
    """Daily ~16:12 ET: mark + close open dip-buy 'candidate' forward-test
    trades (50%-of-max-profit or 10-trading-day hold). Isolated from the core
    ExitManager; wrapped per Standing Rule #10."""
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("dipbuy_resolver: non-trading day, skipping")
        return
    try:
        from learning.dipbuy_forward import resolve_candidates
        spy_close = None
        if polygon_client is not None:
            df = polygon_client.get_bars(
                "SPY", timeframe=config.SWING_PRIMARY_TIMEFRAME, limit=3, days_back=3)
            if df is not None and len(df):
                spy_close = float(df["close"].iloc[-1])
        if spy_close is None:
            logger.warning("dipbuy_resolver: no SPY close — skipping today")
            return
        vix = 16.0
        if vix_client is not None:
            try:
                vix = float(vix_client.get_current())
            except Exception:
                pass
        closed = resolve_candidates(TradeRecorder(), spy_close=spy_close, vix=vix)
        logger.info(f"learning.dipbuy_resolver -> {len(closed)} candidate closes")
    except Exception as e:
        logger.exception(f"learning.dipbuy_resolver failed: {e}")
    # QQQ condor candidates — same slot, separately wrapped (Standing Rule #10)
    try:
        from learning.qqq_condor_forward import resolve_qqq_condors
        from alerts.stop_watchdog import yf_spot
        qqq_spot = yf_spot("QQQ")
        vxn = yf_spot("^VXN") or 20.0
        if qqq_spot:
            closed_q = resolve_qqq_condors(TradeRecorder(), qqq_spot=qqq_spot, vxn=vxn)
            if closed_q:
                logger.info(f"qqq_condor_forward -> {len(closed_q)} candidate closes")
    except Exception as e:
        logger.exception(f"qqq_condor_forward resolver failed: {e}")
    # 7DTE condor candidates — same slot, separately wrapped (Standing Rule #10)
    try:
        from learning.seven_dte_forward import resolve_seven_dte
        from alerts.stop_watchdog import yf_spot
        spy_now = yf_spot("SPY") or spy_close
        vix_now = yf_spot("^VIX") or vix
        if spy_now:
            closed_7 = resolve_seven_dte(TradeRecorder(), spy_spot=spy_now, vix=vix_now)
            if closed_7:
                logger.info(f"seven_dte_forward -> {len(closed_7)} candidate closes")
    except Exception as e:
        logger.exception(f"seven_dte_forward resolver failed: {e}")
    # Broken-wing butterfly candidates — same slot, separately wrapped (Rule #10)
    try:
        from learning.broken_wing_forward import resolve_broken_wing
        from alerts.stop_watchdog import yf_spot
        spy_now = yf_spot("SPY") or spy_close
        vix_now = yf_spot("^VIX") or vix
        if spy_now:
            closed_bwb = resolve_broken_wing(TradeRecorder(), spy_spot=spy_now, vix=vix_now)
            if closed_bwb:
                logger.info(f"broken_wing_forward -> {len(closed_bwb)} candidate closes")
    except Exception as e:
        logger.exception(f"broken_wing_forward resolver failed: {e}")


def job_reflector(post_fn=None):
    if not config.is_trading_day(datetime.now(pytz.timezone("US/Eastern"))):
        logger.info("reflector: non-trading day, skipping")
        return
    try:
        result = Reflector(post_fn=post_fn).reflect_today()
        logger.info(
            f"learning.reflector -> units={result.get('units')} "
            f"failed={result.get('failed')} kb={len(result.get('kb_ids', []))}"
        )
    except Exception as e:
        logger.exception(f"learning.reflector failed: {e}")


def job_hypothesis_engine():
    try:
        spec = HypothesisEngine().propose_weekly()
        logger.info(f"learning.hypothesis_engine -> {spec.get('id') if spec else 'no proposal'}")
    except Exception as e:
        logger.exception(f"learning.hypothesis_engine failed: {e}")


def job_hypothesis_runner(post_fn=None):
    try:
        ran = HypothesisRunner(post_fn=post_fn).run_pending()
        logger.info(f"learning.hypothesis_runner -> {len(ran)} hypotheses processed")
    except Exception as e:
        logger.exception(f"learning.hypothesis_runner failed: {e}")


def job_off_hours_learner():
    try:
        result = OffHoursLearner().run()
        logger.info(f"learning.off_hours_learner -> {result}")
    except Exception as e:
        logger.exception(f"learning.off_hours_learner failed: {e}")


# ── REGISTRATION ──────────────────────────────────────

def register_learning_jobs(
    scheduler,
    polygon_client=None,
    vix_client=None,
    post_fn=None,
    play_fn=None,
    approve_fn=None,
):
    """Register all learning jobs onto an already-running scheduler."""
    from apscheduler.triggers.cron import CronTrigger
    eastern = pytz.timezone("US/Eastern")

    scheduler.add_job(
        job_paper_broker,
        # 09:45 ET — inside the entry window (config.ENTRY_WINDOW_START_ET). Was
        # 09:16 (pre-market); opens must not fire before 15 min after the bell.
        CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=eastern),
        kwargs={"play_fn": play_fn, "approve_fn": approve_fn},
        id="learning_paper_broker",
        name="Learning: paper broker",
        replace_existing=True,
    )

    # RH read-only position sync — every 15 min during market hours so the live
    # book/watchdog track real trades hands-off (no-op until the user has logged
    # in via `python -m learning.rh_sync login`).
    scheduler.add_job(
        job_rh_sync,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/15", timezone=eastern),
        kwargs={"alert_fn": play_fn},
        id="learning_rh_sync",
        name="Learning: RH read-only sync",
        replace_existing=True,
    )

    # Loop health monitor — daily, flags any silently-stale learning artifact.
    scheduler.add_job(
        job_loop_health,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=eastern),
        kwargs={"alert_fn": play_fn},
        id="learning_loop_health",
        name="Learning: loop health monitor",
        replace_existing=True,
    )

    # Daily CSV refresh — every morning after a trading day, before the 08:15
    # brief (T4#14: the weekly Saturday cadence left the forecast/off-hours
    # replay on stale data all week and false-alarmed after holidays).
    scheduler.add_job(
        job_refresh_csv,
        CronTrigger(day_of_week="tue-sat", hour=7, minute=30, timezone=eastern),
        id="learning_refresh_csv",
        name="Learning: daily CSV refresh",
        replace_existing=True,
    )

    scheduler.add_job(
        job_outcome_resolver,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "post_fn": post_fn},
        id="learning_outcome_resolver",
        name="Learning: outcome resolver",
        replace_existing=True,
    )

    scheduler.add_job(
        job_exit_manager,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=8, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "vix_client": vix_client,
                "post_fn": post_fn, "play_fn": play_fn, "dte_buckets": ["45DTE"]},
        id="learning_exit_manager",
        name="Learning: exit manager (daily 45DTE)",
        replace_existing=True,
    )

    # Phase 2b-3: intraday exit cron. Runs every 5 min during market hours,
    # processes only 0DTE/1-3DTE positions. No-op today because paper_broker
    # hardcodes 45DTE; Phase 3's intraday entry pipeline will produce trades
    # this cron then manages.
    scheduler.add_job(
        job_exit_manager,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5",
                    timezone=eastern),
        kwargs={"polygon_client": polygon_client, "vix_client": vix_client,
                "post_fn": post_fn, "play_fn": play_fn, "dte_buckets": ["0DTE", "1-3DTE"]},
        id="learning_exit_manager_intraday",
        name="Learning: exit manager (intraday 0DTE / 1-3DTE)",
        replace_existing=True,
    )

    scheduler.add_job(
        job_expiry_resolver,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "post_fn": post_fn, "play_fn": play_fn},
        id="learning_expiry_resolver",
        name="Learning: expiry resolver",
        replace_existing=True,
    )

    # 16:20 ET — one consolidated, disciplined-only exit digest for the day,
    # after the 16:08 (45DTE), intraday, and 16:10 (expiry) closes have run.
    # 16:12 ET — dip-buy forward-test resolver (before the 16:20 digest picks
    # up its closes). Isolated from the core exit manager.
    scheduler.add_job(
        job_dipbuy_resolver,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=12, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "vix_client": vix_client},
        id="learning_dipbuy_resolver",
        name="Learning: dip-buy forward-test resolver",
        replace_existing=True,
    )

    scheduler.add_job(
        job_exit_digest,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=20, timezone=eastern),
        kwargs={"play_fn": play_fn},
        id="learning_exit_digest",
        name="Learning: exit digest (EOD, disciplined-only)",
        replace_existing=True,
    )

    scheduler.add_job(
        job_reflector,
        CronTrigger(day_of_week="mon-fri", hour=19, minute=1, timezone=eastern),
        kwargs={"post_fn": post_fn},
        id="learning_reflector",
        name="Learning: reflector",
        replace_existing=True,
    )

    scheduler.add_job(
        job_hypothesis_engine,
        CronTrigger(day_of_week="sat", hour=10, minute=0, timezone=eastern),
        id="learning_hypothesis_engine",
        name="Learning: weekly hypothesis",
        replace_existing=True,
    )

    scheduler.add_job(
        job_hypothesis_runner,
        CronTrigger(day_of_week="sat", hour=11, minute=0, timezone=eastern),
        kwargs={"post_fn": post_fn},
        id="learning_hypothesis_runner",
        name="Learning: hypothesis backtest",
        replace_existing=True,
    )

    scheduler.add_job(
        job_off_hours_learner,
        CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=eastern),
        id="learning_off_hours",
        name="Learning: off-hours replay",
        replace_existing=True,
    )

    from learning.meta_recalibrate import run_meta_recalibration
    scheduler.add_job(
        run_meta_recalibration,
        CronTrigger(day_of_week="sat", hour=12, minute=0, timezone=eastern),
        id="learning_meta_recalibration",
        name="Learning: meta-model recalibration",
        replace_existing=True,
    )

    logger.info("Learning jobs registered:")
    logger.info("   09:45 ET (Mon-Fri) - paper broker (entry window)")
    logger.info("   16:05 ET (Mon-Fri) - outcome resolver")
    logger.info("   16:08 ET (Mon-Fri) - exit manager [45DTE daily]")
    logger.info("   every 5 min 9:00-15:55 ET (Mon-Fri) - exit manager [0DTE / 1-3DTE intraday]")
    logger.info("   16:10 ET (Mon-Fri) - expiry resolver")
    logger.info("   19:01 ET (Mon-Fri) - daily reflector")
    logger.info("   Sat 10:00 ET       - hypothesis engine")
    logger.info("   Sat 11:00 ET       - hypothesis runner")
    logger.info("   Sun 10:00 ET       - off-hours learner")
    logger.info("   Sat 12:00 ET       - meta-model recalibration")
