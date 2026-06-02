# tests/test_notifier.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest import mock
from alerts.notifier import Notifier


class _FakePushover:
    def __init__(self): self.sends = []
    def send(self, title, message, url=None, url_title=None, priority=0):
        self.sends.append({"title": title, "message": message, "url": url, "priority": priority})
        return True


def test_play_pushes_high_priority_and_persists(monkeypatch):
    px = _FakePushover()
    saved = {}
    monkeypatch.setattr("alerts.notifier.alert_store.save_alert", lambda a: saved.setdefault("id", "AID42") or "AID42")
    n = Notifier(px)
    n.play({"ticker": "SPY", "tier": "high_conviction"}, title="SPY play", body="opened iron_condor")
    assert len(px.sends) == 1
    assert px.sends[0]["priority"] == 1            # high — makes sound
    assert px.sends[0]["title"] == "SPY play"
    assert saved.get("id") == "AID42"              # persisted for the deep link


def test_play_without_alert_still_pushes(monkeypatch):
    px = _FakePushover()
    n = Notifier(px)
    n.play(title="Target hit", body="F6C4 +$80")   # lifecycle event, no alert dict
    assert len(px.sends) == 1 and px.sends[0]["priority"] == 1


def test_log_does_not_push(monkeypatch):
    px = _FakePushover()
    monkeypatch.setattr("alerts.notifier.alert_store.save_alert", lambda a: "X")
    n = Notifier(px)
    n.log({"ticker": "SPY", "tier": "standard"})
    n.log("morning briefing ...")
    assert px.sends == []                          # the anti-congestion guarantee


def test_alert_and_message_route_to_log_no_push(monkeypatch):
    px = _FakePushover()
    monkeypatch.setattr("alerts.notifier.alert_store.save_alert", lambda a: "X")
    n = Notifier(px)
    n.alert({"ticker": "SPY", "tier": "high_conviction"}, "full card")
    n.message("UOA flow ...")
    assert px.sends == []                          # legacy methods no longer push
