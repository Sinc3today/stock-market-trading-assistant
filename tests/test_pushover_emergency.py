"""tests/test_pushover_emergency.py -- emergency (priority 2) Pushover support.

Pushover REQUIRES retry+expire when priority==2 (it re-alerts until acked).
Without them the API rejects the message, so emergency alerts silently fail.
Mocked — no live network.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from alerts import pushover_client as pc


class _Resp:
    status_code = 200
    text = "ok"


def _enabled_client():
    c = pc.PushoverClient()
    c.enabled, c.token, c.user_key = True, "tok", "usr"
    return c


def test_emergency_priority_includes_retry_and_expire(monkeypatch):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured.update(data or {})
        return _Resp()

    monkeypatch.setattr(pc.requests, "post", fake_post)
    assert _enabled_client().send("Approve trade", "tap to approve", priority=2) is True
    assert captured["priority"] == 2
    assert int(captured["retry"]) >= 30        # Pushover minimum
    assert int(captured["expire"]) <= 10800    # Pushover maximum
    assert int(captured["expire"]) >= int(captured["retry"])


def test_normal_priority_omits_retry_and_expire(monkeypatch):
    captured = {}
    monkeypatch.setattr(pc.requests, "post",
                        lambda url, data=None, timeout=None: (captured.update(data or {}), _Resp())[1])
    _enabled_client().send("info", "fyi", priority=0)
    assert "retry" not in captured
    assert "expire" not in captured
