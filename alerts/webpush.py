"""alerts/webpush.py -- PWA Web Push channel (hybrid with Pushover).

The user wanted notifications that open straight into the (PWA-installed) web
app. Web Push has no Pushover-style emergency mode, so the hybrid policy
(2026-07-09) is enforced by MuxSender:

    priority >= 2  -> Pushover AND PWA push   (entry-approve, stops, recovery —
                                               the can't-miss alerts keep their
                                               nag-until-ack channel)
    priority <  2  -> PWA push only; falls back to Pushover until the first
                      device subscribes, so nothing is lost mid-rollout.

VAPID keys are self-generated and persisted under LOG_DIR (never committed —
logs/ is gitignored). Subscriptions live in a small atomic JSON file; dead
endpoints (404/410 from the push service) are pruned automatically.
"""
from __future__ import annotations

import json
import os

from loguru import logger

import config
from atomic_io import atomic_write_text


def _path(name: str) -> str:
    return os.path.join(config.LOG_DIR, name)


# ── VAPID keys (generated once) ─────────────────────────────────────────────

def vapid_keys() -> dict:
    p = _path("webpush_vapid.json")
    if os.path.exists(p):
        return json.load(open(p))
    from py_vapid import Vapid02, b64urlencode
    from cryptography.hazmat.primitives import serialization
    v = Vapid02()
    v.generate_keys()
    priv = v.private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).hex()
    raw_pub = v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)
    keys = {"private_key": priv, "public_key": b64urlencode(raw_pub)}
    atomic_write_text(p, json.dumps(keys))
    logger.info("webpush: generated VAPID keypair")
    return keys


def _vapid_private_pem_path() -> str:
    """pywebpush wants a PEM FILE PATH (passing PEM text makes it try to parse
    the text as a raw base64url key -> 'ASN.1 parsing error: invalid length',
    the bug that silently killed every send on 07-09/10). Materialize the PEM
    from the stored DER once and hand over the path. Same keypair — existing
    device subscriptions stay valid."""
    pem_path = _path("webpush_vapid_private.pem")
    if not os.path.exists(pem_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import load_der_private_key
        key = load_der_private_key(bytes.fromhex(vapid_keys()["private_key"]),
                                   password=None)
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        atomic_write_text(pem_path, pem)
    return pem_path


# ── Subscription store ──────────────────────────────────────────────────────

class SubscriptionStore:
    def __init__(self):
        self.path = _path("webpush_subscriptions.json")

    def all(self) -> list[dict]:
        try:
            return json.load(open(self.path))
        except (OSError, json.JSONDecodeError):
            return []

    def add(self, sub: dict) -> bool:
        subs = self.all()
        if any(s.get("endpoint") == sub.get("endpoint") for s in subs):
            return False
        subs.append(sub)
        atomic_write_text(self.path, json.dumps(subs, indent=1))
        logger.info(f"webpush: subscription added ({len(subs)} device(s))")
        return True

    def remove(self, endpoint: str) -> None:
        subs = [s for s in self.all() if s.get("endpoint") != endpoint]
        atomic_write_text(self.path, json.dumps(subs, indent=1))


# ── Sending ─────────────────────────────────────────────────────────────────

def send_push(*, title: str, body: str, url: str | None = None,
              tag: str | None = None) -> int:
    """Push to every subscribed device. Returns the delivered count; prunes
    dead subscriptions. Never raises."""
    store = SubscriptionStore()
    subs = store.all()
    if not subs:
        return 0
    from pywebpush import webpush, WebPushException
    payload = json.dumps({"title": title, "body": body,
                          "url": url or "/copilot", "tag": tag or "smta"})
    pem_path = _vapid_private_pem_path()
    sent = 0
    for s in subs:
        try:
            webpush(subscription_info=s, data=payload,
                    vapid_private_key=pem_path,
                    # Apple's push service rejects invalid contact claims (BadJwtToken,
                    # 2026-07-10) — must be a real mailto/https.
                    vapid_claims={"sub": "mailto:alex.rodriguez91.ar@gmail.com"},
                    ttl=3600)
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                store.remove(s.get("endpoint"))
                logger.info("webpush: pruned dead subscription")
            else:
                logger.warning(f"webpush send failed: {e}")
        except Exception as e:
            logger.warning(f"webpush send failed: {e}")
    return sent


class MuxSender:
    """Drop-in for PushoverClient at alert call sites — enforces the hybrid
    policy. Exposes .send(...) with the same signature."""

    def __init__(self, pushover):
        self.pushover = pushover

    def send(self, title: str, message: str, url: str | None = None,
             url_title: str | None = None, priority: int = 0, **kw) -> bool:
        base = (getattr(config, "PWA_BASE_URL", "") or "").rstrip("/")
        open_url = url or (f"{base}/copilot" if base else "/copilot")
        delivered = send_push(title=title, body=message, url=open_url,
                              tag=title[:32])
        if priority >= 2:
            # emergency class: Pushover keeps the nag-until-ack guarantee
            return bool(self.pushover.send(title, message, url=url,
                                           url_title=url_title,
                                           priority=priority, **kw)) or bool(delivered)
        if delivered == 0 and self.pushover:
            # rollout fallback: no PWA devices yet -> don't drop the alert
            return bool(self.pushover.send(title, message, url=url,
                                           url_title=url_title,
                                           priority=priority, **kw))
        return bool(delivered)
