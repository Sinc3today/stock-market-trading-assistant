"""tests/test_logging_bridge.py -- a silently skipped trading day must be visible.

APScheduler logs through stdlib `logging`. Nothing in this project installed a
root handler, so its records fell to `logging.lastResort` -> stderr only, and
NEVER reached logs/app.log. loop_health.scan_recent_log_errors globs app*.log,
so it was structurally incapable of seeing:

  * any uncaught exception inside a scheduled job
  * "Run time of job ... was missed"  (misfire_grace_time=600, so a restart at
    09:56 silently drops the 09:45 paper_broker fire — no trade that day)

grep confirms no app log has ever contained either phrase. This is the
failure mode where the bot looks perfectly healthy and simply does nothing.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.logging_bridge import (
    InterceptHandler, attach_scheduler_listeners, install_stdlib_bridge,
)


@pytest.fixture
def captured():
    """Collect what loguru actually receives."""
    from loguru import logger
    seen = []
    sink = logger.add(lambda m: seen.append(m), level="DEBUG", format="{level}|{message}")
    yield seen
    logger.remove(sink)


@pytest.fixture(autouse=True)
def _clean_root():
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    yield
    root.handlers[:] = before
    root.setLevel(level)


def test_stdlib_records_reach_loguru_after_install(captured):
    install_stdlib_bridge()
    logging.getLogger("apscheduler.executors.default").error("job raised an exception")
    assert any("job raised an exception" in str(m) for m in captured)


def test_a_missed_job_warning_is_captured(captured):
    """The exact phrase APScheduler emits when a fire is dropped."""
    install_stdlib_bridge()
    logging.getLogger("apscheduler.scheduler").warning(
        "Run time of job \"paper_broker\" was missed by 0:11:23")
    assert any("was missed" in str(m) for m in captured)


def test_levels_are_preserved(captured):
    install_stdlib_bridge()
    log = logging.getLogger("some.lib")
    log.error("an error")
    log.warning("a warning")
    joined = " ".join(str(m) for m in captured)
    assert "ERROR" in joined and "WARNING" in joined


def test_install_is_idempotent():
    install_stdlib_bridge()
    install_stdlib_bridge()
    root = logging.getLogger()
    n = sum(1 for h in root.handlers if isinstance(h, InterceptHandler))
    assert n == 1, "installing twice must not double every log line"


def test_noisy_third_party_debug_is_not_forwarded(captured):
    """Bridging at DEBUG would flood app.log with urllib3/matplotlib chatter."""
    install_stdlib_bridge()
    logging.getLogger("urllib3.connectionpool").debug("Starting new HTTPS connection")
    assert not any("Starting new HTTPS" in str(m) for m in captured)


# ── scheduler listeners ──────────────────────────────────────────

class _FakeScheduler:
    def __init__(self):
        self.listeners = []

    def add_listener(self, fn, mask):
        self.listeners.append((fn, mask))

    def fire(self, event):
        for fn, _ in self.listeners:
            fn(event)


class _Event:
    def __init__(self, job_id, exception=None, scheduled_run_time=None):
        self.job_id = job_id
        self.exception = exception
        self.traceback = None
        self.scheduled_run_time = scheduled_run_time


def test_job_error_is_logged_with_the_job_id(captured):
    s = _FakeScheduler()
    attach_scheduler_listeners(s)
    s.fire(_Event("learning_paper_broker", exception=RuntimeError("boom")))
    joined = " ".join(str(m) for m in captured)
    assert "learning_paper_broker" in joined and "boom" in joined


def test_missed_job_is_logged_as_an_error_not_a_shrug(captured):
    """A dropped fire means no trade that day — that is an ERROR, not INFO."""
    s = _FakeScheduler()
    attach_scheduler_listeners(s)
    s.fire(_Event("learning_paper_broker", scheduled_run_time="2026-09-08 09:45"))
    joined = " ".join(str(m) for m in captured)
    assert "ERROR" in joined and "learning_paper_broker" in joined


def test_listeners_are_registered_for_both_event_types():
    s = _FakeScheduler()
    attach_scheduler_listeners(s)
    assert s.listeners, "no listener registered"
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
    mask = s.listeners[0][1]
    assert mask & EVENT_JOB_ERROR and mask & EVENT_JOB_MISSED


# ── loop_health can now see it ───────────────────────────────────

def _log_line(level: str, msg: str) -> str:
    """A line in the real loguru file format the health scan parses."""
    return (f"2026-09-08 09:56:01.123 | {level:<8} | "
            f"runtime.logging_bridge:_on_event:95 - {msg}\n")


def test_loop_health_flags_a_missed_job_on_the_first_occurrence():
    """A once-daily job produces exactly ONE occurrence per 24h window, so the
    3-repeat threshold meant the 09:45 broker could fail every day forever."""
    from learning.loop_health import summarize_error_lines
    from runtime.logging_bridge import MISSED_MARKER
    out = summarize_error_lines([_log_line(
        "ERROR", f"scheduler: job 'learning_paper_broker' {MISSED_MARKER} "
                 "at 2026-09-08 09:45 — that work did NOT run today")])
    assert out, "a missed fire must surface even as a single occurrence"


def test_loop_health_flags_a_single_uncaught_job_exception():
    from learning.loop_health import summarize_error_lines
    from runtime.logging_bridge import JOB_ERROR_MARKER
    out = summarize_error_lines([_log_line(
        "ERROR", f"scheduler: job 'spy_entry' {JOB_ERROR_MARKER}: boom")])
    assert out


def test_routine_errors_still_need_to_repeat():
    """The always-report list must stay narrow, or the daily push becomes noise."""
    from learning.loop_health import summarize_error_lines
    one = [_log_line("ERROR", "FRED observation error for UNRATE: HTTPError")]
    assert summarize_error_lines(one) == []
    assert summarize_error_lines(one * 3)


def test_a_dropped_job_is_ranked_above_routine_noise():
    from learning.loop_health import summarize_error_lines
    from runtime.logging_bridge import MISSED_MARKER
    lines = [_log_line("ERROR", "FRED observation error for X: HTTPError")] * 8
    lines += [_log_line("ERROR", f"scheduler: job 'paper_broker' {MISSED_MARKER} at 09:45")]
    out = summarize_error_lines(lines)
    assert out and "scheduler" in out[0].lower(), \
        "a dropped fire must not be crowded out by repeated routine errors"


# ── it must actually be installed ────────────────────────────────

def test_main_installs_the_bridge_and_the_listeners():
    """A bridge nobody installs is decoration. This is the wiring the audit
    found missing — no InterceptHandler existed anywhere in the tree."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "main.py"
    text = src.read_text()
    assert "install_stdlib_bridge()" in text
    assert "attach_scheduler_listeners(scheduler)" in text
    assert text.index("attach_scheduler_listeners") < text.index("scheduler.start()"), \
        "listeners must be attached before the scheduler starts"
