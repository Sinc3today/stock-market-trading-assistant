"""runtime/logging_bridge.py -- make library failures visible.

The bot runs unattended. Its worst failure mode is not a crash — it is looking
perfectly healthy while doing nothing.

APScheduler logs through the stdlib `logging` module. This project configures
only loguru, and never installed a root handler, so APScheduler's records fell
to `logging.lastResort` -> stderr and never reached `logs/app.log`. Since
`loop_health.scan_recent_log_errors` reads `app*.log`, it was structurally
incapable of noticing:

  * an uncaught exception inside any scheduled job
  * "Run time of job ... was missed" — with `misfire_grace_time=600`, a restart
    at 09:56 silently DROPS the 09:45 paper-broker fire. No trade that day, no
    error anywhere the monitor can see.

grep confirmed no app log has ever contained either phrase — not because they
never happened, but because they could never be written.

Two parts:
  install_stdlib_bridge()      route stdlib logging into loguru
  attach_scheduler_listeners() explicit, greppable ERROR lines per job event
"""
from __future__ import annotations

import logging

from loguru import logger

# Bridge at WARNING, not DEBUG: urllib3, matplotlib and botocore emit thousands
# of DEBUG lines an hour and would bury the signal this exists to surface.
DEFAULT_LEVEL = logging.WARNING

# Loud enough that loop_health's summariser can match on them, and distinct
# enough to grep for in an incident.
MISSED_MARKER = "MISSED its scheduled fire"
JOB_ERROR_MARKER = "job raised an uncaught exception"


class InterceptHandler(logging.Handler):
    """Forward a stdlib LogRecord to loguru, preserving level and origin."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Walk out of the logging machinery so the reported file/line is the
        # caller's, not this handler's.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, f"[{record.name}] {record.getMessage()}"
        )


def install_stdlib_bridge(level: int = DEFAULT_LEVEL) -> None:
    """Route stdlib logging into loguru. Safe to call more than once."""
    root = logging.getLogger()
    if any(isinstance(h, InterceptHandler) for h in root.handlers):
        return                      # idempotent: double-install doubles every line
    root.addHandler(InterceptHandler())
    root.setLevel(level)
    # APScheduler is the one we actually care about; make sure nothing upstream
    # has silenced it.
    for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors"):
        logging.getLogger(name).setLevel(level)
    logger.info("stdlib logging bridged into loguru "
                f"(level {logging.getLevelName(level)})")


def attach_scheduler_listeners(scheduler) -> None:
    """Log job failures and missed fires explicitly.

    The bridge alone would carry APScheduler's own messages, but these give a
    stable, greppable phrasing with the job id — and a MISSED fire is an ERROR
    here rather than the WARNING APScheduler calls it, because a dropped
    09:45 fire means no trade that day.
    """
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    def _on_event(event):
        job_id = getattr(event, "job_id", "?")
        if getattr(event, "exception", None) is not None:
            logger.error(
                f"scheduler: job {job_id!r} {JOB_ERROR_MARKER}: {event.exception}"
            )
            tb = getattr(event, "traceback", None)
            if tb:
                logger.error(f"scheduler: {job_id} traceback:\n{tb}")
        else:
            when = getattr(event, "scheduled_run_time", "?")
            logger.error(
                f"scheduler: job {job_id!r} {MISSED_MARKER} at {when} — "
                "that work did NOT run today"
            )

    scheduler.add_listener(_on_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    logger.info("scheduler job-error / missed-fire listeners attached")
