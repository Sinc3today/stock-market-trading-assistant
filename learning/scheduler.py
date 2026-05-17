"""
learning/scheduler.py -- Register self-learning jobs onto the main APScheduler.

Call register_learning_jobs(scheduler, polygon_client, post_fn) from main.py
*after* the SPY daily jobs are already registered. This adds:

    09:16 ET (Mon-Fri)  paper_broker.execute_today()
    16:05 ET (Mon-Fri)  outcome_resolver.resolve_today()
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
from learning.outcome_resolver  import OutcomeResolver
from learning.reflector         import Reflector
from learning.hypothesis_engine import HypothesisEngine
from learning.hypothesis_runner import HypothesisRunner
from learning.off_hours_learner import OffHoursLearner


# ── JOB WRAPPERS ──────────────────────────────────────

def job_paper_broker():
    try:
        result = PaperBroker().execute_today()
        logger.info(f"learning.paper_broker -> {result}")
    except Exception as e:
        logger.exception(f"learning.paper_broker failed: {e}")


def job_outcome_resolver(polygon_client):
    try:
        result = OutcomeResolver(polygon_client=polygon_client).resolve_today()
        logger.info(f"learning.outcome_resolver -> {result}")
    except Exception as e:
        logger.exception(f"learning.outcome_resolver failed: {e}")


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


def job_hypothesis_runner():
    try:
        ran = HypothesisRunner().run_pending()
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
    post_fn=None,
):
    """Register all six learning jobs onto an already-running scheduler."""
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
        kwargs={"polygon_client": polygon_client},
        id="learning_outcome_resolver",
        name="Learning: outcome resolver",
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

    logger.info("Learning jobs registered:")
    logger.info("   09:16 ET (Mon-Fri) - paper broker")
    logger.info("   16:05 ET (Mon-Fri) - outcome resolver")
    logger.info("   19:01 ET (Mon-Fri) - daily reflector")
    logger.info("   Sat 10:00 ET       - hypothesis engine")
    logger.info("   Sat 11:00 ET       - hypothesis runner")
    logger.info("   Sun 10:00 ET       - off-hours learner")
