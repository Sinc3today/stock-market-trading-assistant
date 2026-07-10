"""tests/test_webpush.py -- PWA Web Push: subscription store + hybrid routing.

Hybrid policy (user, 2026-07-09): priority >=2 (entry-approve, stop watchdog,
recovery) -> Pushover AND PWA push; everything else -> PWA only, falling back to
Pushover until the first device subscribes (so nothing is ever lost mid-rollout).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _sub(endpoint="https://push.example/abc"):
    return {"endpoint": endpoint, "keys": {"p256dh": "BKx", "auth": "aa"}}


def test_store_add_dedupe_remove(iso):
    from alerts.webpush import SubscriptionStore
    s = SubscriptionStore()
    assert s.add(_sub()) is True
    assert s.add(_sub()) is False          # same endpoint -> dedupe
    assert len(s.all()) == 1
    s.remove("https://push.example/abc")
    assert s.all() == []


def test_store_survives_reload(iso):
    from alerts.webpush import SubscriptionStore
    SubscriptionStore().add(_sub())
    assert len(SubscriptionStore().all()) == 1


def test_vapid_keys_persist(iso):
    from alerts.webpush import vapid_keys
    k1 = vapid_keys()
    k2 = vapid_keys()
    assert k1["public_key"] == k2["public_key"]      # generated once, reused
    assert k1["private_key"] and k1["public_key"]


def test_vapid_private_key_loads_the_way_pywebpush_does(iso):
    # Regression (2026-07-10): passing PEM TEXT to pywebpush made it parse the
    # text as a raw key -> ASN.1 error -> every send silently failed (Pushover
    # fallback masked it). pywebpush loads a PEM *file* via Vapid.from_file —
    # assert our materialized file actually loads through that exact path.
    from alerts.webpush import _vapid_private_pem_path, vapid_keys
    from py_vapid import Vapid
    path = _vapid_private_pem_path()
    assert os.path.exists(path)
    v = Vapid.from_file(path)                       # pywebpush's load path
    assert v.private_key is not None
    # and it's the SAME keypair the clients subscribed with
    from py_vapid import b64urlencode
    from cryptography.hazmat.primitives import serialization
    raw_pub = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert b64urlencode(raw_pub) == vapid_keys()["public_key"]


class _FakePush:
    def __init__(self): self.sent = []
    def send(self, title, message, url=None, url_title=None, priority=0, **k):
        self.sent.append((title, priority)); return True


def test_mux_emergency_goes_to_both(iso, monkeypatch):
    import alerts.webpush as wp
    sent_web = []
    monkeypatch.setattr(wp, "send_push", lambda **k: sent_web.append(k) or 1)
    wp.SubscriptionStore().add(_sub())
    po = _FakePush()
    mux = wp.MuxSender(po)
    mux.send("🛑 Close condor", "SPY at strike", priority=2)
    assert len(po.sent) == 1 and po.sent[0][1] == 2   # pushover kept
    assert len(sent_web) == 1                          # PWA too


def test_mux_normal_goes_pwa_only_when_subscribed(iso, monkeypatch):
    import alerts.webpush as wp
    sent_web = []
    monkeypatch.setattr(wp, "send_push", lambda **k: sent_web.append(k) or 1)
    wp.SubscriptionStore().add(_sub())
    po = _FakePush()
    wp.MuxSender(po).send("⏳ 14 DTE", "time exit", priority=1)
    assert po.sent == []                               # no pushover buzz
    assert len(sent_web) == 1


def test_mux_falls_back_to_pushover_with_no_subscribers(iso, monkeypatch):
    import alerts.webpush as wp
    monkeypatch.setattr(wp, "send_push", lambda **k: 0)   # no devices
    po = _FakePush()
    wp.MuxSender(po).send("⏳ 14 DTE", "time exit", priority=1)
    assert len(po.sent) == 1                           # nothing lost mid-rollout
