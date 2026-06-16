"""tests/test_watchdog.py -- liveness watchdog (auto-restart pings + recovery alert).

After the 2026-06-15 silent freeze: a hung bot must (1) stop pinging so systemd
restarts it, and (2) fire an emergency alert on recovery so it's never silent.
Pure-logic tests; sd_notify (socket) is exercised only as a no-op.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from alerts import watchdog


def test_stamp_and_read_roundtrip(tmp_path):
    p = str(tmp_path / "hb.txt")
    watchdog.stamp_heartbeat(p, now=1000.0)
    assert watchdog.last_heartbeat(p) == 1000.0


def test_last_heartbeat_missing_file_is_none(tmp_path):
    assert watchdog.last_heartbeat(str(tmp_path / "nope.txt")) is None


def test_gap_seconds():
    assert watchdog.gap_seconds(None) is None
    assert watchdog.gap_seconds(1000.0, now=1300.0) == 300.0


def test_recovery_message_mentions_minutes():
    msg = watchdog.recovery_alert_message(600)
    assert "10-min" in msg


class _Spy:
    def __init__(self): self.calls = []
    def send(self, title, message, priority=0, **kw):
        self.calls.append({"title": title, "priority": priority}); return True


def test_check_recovery_alerts_on_stale_gap(tmp_path):
    p = str(tmp_path / "hb.txt")
    watchdog.stamp_heartbeat(p, now=1000.0)
    spy = _Spy()
    # now is 1000 + 5min = 1300, threshold 240 -> stale -> alert (emergency)
    fired = watchdog.check_recovery(spy, path=p, now=1300.0, threshold_s=240)
    assert fired is True
    assert spy.calls and spy.calls[0]["priority"] == 2


def test_check_recovery_silent_on_fresh_gap(tmp_path):
    p = str(tmp_path / "hb.txt")
    watchdog.stamp_heartbeat(p, now=1000.0)
    spy = _Spy()
    # only 30s gap (a clean restart) -> no alert
    assert watchdog.check_recovery(spy, path=p, now=1030.0, threshold_s=240) is False
    assert spy.calls == []


def test_check_recovery_silent_on_first_ever_boot(tmp_path):
    # no heartbeat file yet -> can't know we were down -> no false alert
    spy = _Spy()
    assert watchdog.check_recovery(spy, path=str(tmp_path / "hb.txt"), now=1.0) is False


def test_sd_notify_noop_without_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    watchdog.sd_notify("WATCHDOG=1")   # must not raise
