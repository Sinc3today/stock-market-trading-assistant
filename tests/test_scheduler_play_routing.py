"""
tests/test_scheduler_play_routing.py -- exit-notification routing.

Exits are NOT pushed per-run anymore. job_exit_manager and job_expiry_resolver
still EXECUTE closes but stay SILENT (no play_fn, no post_fn). All exit
notification is consolidated into the end-of-day disciplined-only digest
(job_exit_digest). See learning/scheduler.py.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest import mock
import learning.scheduler as sch


def test_exit_manager_job_is_silent(monkeypatch):
    """job_exit_manager closes trades but pushes nothing (digest handles it)."""
    plays = []
    monkeypatch.setattr(
        sch,
        "ExitManager",
        mock.Mock(return_value=mock.Mock(
            manage_open=mock.Mock(
                return_value=[{"trade_id": "T1", "outcome": "win", "pnl_dollars": 80.0}]
            )
        )),
    )
    monkeypatch.setattr(sch.config, "is_trading_day", lambda *_: True)
    sch.job_exit_manager(
        polygon_client=mock.Mock(),
        vix_client=mock.Mock(),
        post_fn=lambda m: plays.append(("post", m)),
        play_fn=lambda **kw: plays.append(("play", kw.get("body"))),
        dte_buckets=["45DTE"],
    )
    assert plays == []  # no per-run push of any kind


def test_expiry_job_is_silent(monkeypatch):
    """job_expiry_resolver closes expired trades but pushes nothing."""
    plays = []
    monkeypatch.setattr(
        sch,
        "ExpiryResolver",
        mock.Mock(return_value=mock.Mock(
            resolve_expired=mock.Mock(return_value=[{"trade_id": "T2"}])
        )),
    )
    monkeypatch.setattr(sch.config, "is_trading_day", lambda *_: True)
    sch.job_expiry_resolver(
        polygon_client=mock.Mock(),
        post_fn=lambda m: plays.append(("post", m)),
        play_fn=lambda **kw: plays.append(("play", kw.get("body"))),
    )
    assert plays == []  # no per-run push of any kind
