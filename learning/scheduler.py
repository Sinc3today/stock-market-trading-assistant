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
from loguru import logger

from learning.paper_broker      import PaperBroker
from learning.outcome_resolver  import OutcomeResolver, format_resolved_message
from learning.predictions       import PredictionLog
from learning.reflector         import Reflector
from learning.hypothesis_engine import HypothesisEngine
from learning.hypothesis_runner import HypothesisRunner
from learning.off_hours_learner import OffHoursLearner
from learning.expiry_resolver   import ExpiryResolver, format_expiry_message
from learning.exit_manager      import ExitManager, format_exit_message


# ── JOB WRAPPERS ──────────────────────────────────────

def job_paper_broker():
    try:
        result = PaperBroker().execute_today()
        logger.info(f"learning.paper_broker -> {result}")
    except Exception as e:
        logger.exception(f"learning.paper_broker failed: {e}")


def job_outcome_resolver(polygon_client, post_fn=None):
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
                     dte_buckets=None):
    try:
        closed = ExitManager(
            polygon_client=polygon_client, vix_client=vix_client,
        ).manage_open(dte_buckets=dte_buckets)
        logger.info(f"learning.exit_manager [{dte_buckets or 'all'}] -> {len(closed)} closed")
        if closed and post_fn:
            try:
                post_fn(format_exit_message(closed))
            except Exception as e:
                logger.warning(f"learning.exit_manager notify failed: {e}")
    except Exception as e:
        logger.exception(f"learning.exit_manager failed: {e}")


def job_expiry_resolver(polygon_client, post_fn=None):
    try:
        closed = ExpiryResolver(polygon_client=polygon_client).resolve_expired()
        logger.info(f"learning.expiry_resolver -> {len(closed)} closed")
        if closed and post_fn:
            try:
                post_fn(format_expiry_message(closed))
            except Exception as e:
                logger.warning(f"learning.expiry_resolver notify failed: {e}")
    except Exception as e:
        logger.exception(f"learning.expiry_resolver failed: {e}")


def job_reflector(post_fn=None):
    try:
        result = Reflector(post_fn=post_fn).reflect_today()
        logger.info(
            f"learning.reflector -> md={result.get('markdown')} "
            f"kb={len(result.get('kb_ids', []))} parsed={result.get('parsed')}"
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
):
    """Register all learning jobs onto an already-running scheduler."""
    from apscheduler.triggers.cron import CronTrigger
    eastern = pytz.timezone("US/Eastern")

    scheduler.add_job(
        job_paper_broker,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=16, timezone=eastern),
        id="learning_paper_broker",
        name="Learning: paper broker",
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
                "post_fn": post_fn, "dte_buckets": ["45DTE"]},
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
                "post_fn": post_fn, "dte_buckets": ["0DTE", "1-3DTE"]},
        id="learning_exit_manager_intraday",
        name="Learning: exit manager (intraday 0DTE / 1-3DTE)",
        replace_existing=True,
    )

    scheduler.add_job(
        job_expiry_resolver,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "post_fn": post_fn},
        id="learning_expiry_resolver",
        name="Learning: expiry resolver",
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
    logger.info("   09:16 ET (Mon-Fri) - paper broker")
    logger.info("   16:05 ET (Mon-Fri) - outcome resolver")
    logger.info("   16:08 ET (Mon-Fri) - exit manager [45DTE daily]")
    logger.info("   every 5 min 9:00-15:55 ET (Mon-Fri) - exit manager [0DTE / 1-3DTE intraday]")
    logger.info("   16:10 ET (Mon-Fri) - expiry resolver")
    logger.info("   19:01 ET (Mon-Fri) - daily reflector")
    logger.info("   Sat 10:00 ET       - hypothesis engine")
    logger.info("   Sat 11:00 ET       - hypothesis runner")
    logger.info("   Sun 10:00 ET       - off-hours learner")
    logger.info("   Sat 12:00 ET       - meta-model recalibration")
