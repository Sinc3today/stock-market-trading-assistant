"""
tests/test_scheduler_play_routing.py -- play_fn threading to lifecycle jobs.

Verifies that job_exit_manager and job_expiry_resolver call play_fn(title=,
body=) on urgent events (target/stop hit, expiry close), and that post_fn is
NOT called for those events (play_fn takes over).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest import mock
import learning.scheduler as sch


def test_exit_manager_job_uses_play_fn(monkeypatch):
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
    monkeypatch.setattr(sch, "format_exit_message", lambda closed: "target hit T1 +$80")
    monkeypatch.setattr(sch.config, "is_trading_day", lambda *_: True)
    sch.job_exit_manager(
        polygon_client=mock.Mock(),
        vix_client=mock.Mock(),
        post_fn=lambda m: plays.append(("post", m)),
        play_fn=lambda **kw: plays.append(("play", kw.get("body"))),
        dte_buckets=["45DTE"],
    )
    assert ("play", "target hit T1 +$80") in plays
    assert not any(tag == "post" for tag, _ in plays)  # lifecycle goes to play, not post


def test_expiry_job_uses_play_fn(monkeypatch):
    plays = []
    monkeypatch.setattr(
        sch,
        "ExpiryResolver",
        mock.Mock(return_value=mock.Mock(
            resolve_expired=mock.Mock(return_value=[{"trade_id": "T2"}])
        )),
    )
    monkeypatch.setattr(sch, "format_expiry_message", lambda closed: "expiry closed T2")
    monkeypatch.setattr(sch.config, "is_trading_day", lambda *_: True)
    sch.job_expiry_resolver(
        polygon_client=mock.Mock(),
        post_fn=lambda m: plays.append(("post", m)),
        play_fn=lambda **kw: plays.append(("play", kw.get("body"))),
    )
    assert ("play", "expiry closed T2") in plays
    assert not any(tag == "post" for tag, _ in plays)  # lifecycle goes to play, not post
