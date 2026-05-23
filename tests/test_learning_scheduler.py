"""
tests/test_learning_scheduler.py -- register_learning_jobs wiring + job wrappers.

These tests use a fake APScheduler that records add_job calls instead of
running anything. Job wrappers are tested in isolation: each one catches
exceptions from its underlying module so a single failure can't crash the bot.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import scheduler as sched


class FakeScheduler:
    """Captures add_job calls for assertion."""
    def __init__(self):
        self.jobs = []
    def add_job(self, fn, trigger, **kwargs):
        self.jobs.append({"fn": fn, "trigger": trigger, **kwargs})


def test_register_learning_jobs_adds_all_jobs():
    s = FakeScheduler()
    sched.register_learning_jobs(s, polygon_client=None, post_fn=None)
    assert len(s.jobs) == 10
    job_ids = {j["id"] for j in s.jobs}
    assert job_ids == {
        "learning_paper_broker",
        "learning_outcome_resolver",
        "learning_exit_manager",
        "learning_expiry_resolver",
        "learning_reflector",
        "learning_hypothesis_engine",
        "learning_hypothesis_runner",
        "learning_off_hours",
        "learning_meta_recalibration",
        "learning_exit_manager_intraday",     # NEW
    }


def test_exit_manager_job_receives_polygon_vix_and_post_fn():
    """Exit manager needs SPY close (polygon), VIX (for the BS mark), and a
    notifier (post_fn)."""
    s = FakeScheduler()
    polygon, vixc = object(), object()
    post_fn = lambda msg: None
    sched.register_learning_jobs(s, polygon_client=polygon, vix_client=vixc, post_fn=post_fn)

    job = next(j for j in s.jobs if j["id"] == "learning_exit_manager")
    assert job["kwargs"]["polygon_client"] is polygon
    assert job["kwargs"]["vix_client"]     is vixc
    assert job["kwargs"]["post_fn"]        is post_fn


def test_expiry_resolver_job_receives_polygon_client_and_post_fn():
    """Expiry resolver needs SPY close (polygon) and a notifier (post_fn)."""
    s = FakeScheduler()
    polygon = object()
    post_fn = lambda msg: None
    sched.register_learning_jobs(s, polygon_client=polygon, post_fn=post_fn)

    expiry_job = next(j for j in s.jobs if j["id"] == "learning_expiry_resolver")
    assert expiry_job["kwargs"]["polygon_client"] is polygon
    assert expiry_job["kwargs"]["post_fn"]        is post_fn


def test_outcome_resolver_job_receives_polygon_client_and_post_fn():
    """The outcome resolver job needs polygon_client (to fetch SPY close) and
    post_fn (to ping the resolved notification at 16:05). Verify kwargs."""
    s = FakeScheduler()
    polygon = object()
    post_fn = lambda msg: None
    sched.register_learning_jobs(s, polygon_client=polygon, post_fn=post_fn)

    resolver_job = next(j for j in s.jobs if j["id"] == "learning_outcome_resolver")
    assert resolver_job["kwargs"]["polygon_client"] is polygon
    assert resolver_job["kwargs"]["post_fn"]        is post_fn


def test_reflector_job_receives_post_fn():
    s = FakeScheduler()
    post_fn = lambda msg: None
    sched.register_learning_jobs(s, polygon_client=None, post_fn=post_fn)

    reflector_job = next(j for j in s.jobs if j["id"] == "learning_reflector")
    assert reflector_job["kwargs"]["post_fn"] is post_fn


def test_job_paper_broker_swallows_exceptions(monkeypatch):
    """A crashing PaperBroker must not bubble out of the job wrapper."""
    class Boom:
        def execute_today(self):
            raise RuntimeError("paper broker exploded")
    monkeypatch.setattr(sched, "PaperBroker", lambda: Boom())
    # Should not raise.
    sched.job_paper_broker()


def test_job_outcome_resolver_swallows_exceptions(monkeypatch):
    class Boom:
        def __init__(self, **kwargs): pass
        def resolve_today(self):
            raise RuntimeError("resolver exploded")
    monkeypatch.setattr(sched, "OutcomeResolver", Boom)
    sched.job_outcome_resolver(polygon_client=None, post_fn=None)


def test_job_reflector_swallows_exceptions(monkeypatch):
    class Boom:
        def __init__(self, **kwargs): pass
        def reflect_today(self):
            raise RuntimeError("reflector exploded")
    monkeypatch.setattr(sched, "Reflector", Boom)
    sched.job_reflector(post_fn=None)


def test_job_hypothesis_engine_swallows_exceptions(monkeypatch):
    class Boom:
        def propose_weekly(self):
            raise RuntimeError("hypothesis engine exploded")
    monkeypatch.setattr(sched, "HypothesisEngine", lambda: Boom())
    sched.job_hypothesis_engine()


def test_job_hypothesis_runner_swallows_exceptions(monkeypatch):
    class Boom:
        def run_pending(self):
            raise RuntimeError("runner exploded")
    monkeypatch.setattr(sched, "HypothesisRunner", lambda: Boom())
    sched.job_hypothesis_runner()


def test_job_off_hours_learner_swallows_exceptions(monkeypatch):
    class Boom:
        def run(self):
            raise RuntimeError("off-hours exploded")
    monkeypatch.setattr(sched, "OffHoursLearner", lambda: Boom())
    sched.job_off_hours_learner()
