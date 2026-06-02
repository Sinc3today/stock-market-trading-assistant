"""
alerts/notifier.py -- Pushover-only notification router.

Two intents:
    play(alert=None, *, title, body)  -> push (priority 1) + persist; the ONLY
                                          path that reaches the phone. Used for
                                          actionable plays (disciplined open,
                                          target/stop hit, expiry close).
    log(message_or_alert)             -> record only (persist alerts to the
                                          store, logger.info); NO push.

.alert()/.message() are kept as silent wrappers over log() so existing call
sites keep working without pushing — push is opt-in. Discord was removed
2026-06-02 (see the spec).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

from alerts import alert_store
from alerts.pushover_client import PushoverClient, _build_alert_url

PLAY_PRIORITY = 1   # Pushover high — makes sound


class Notifier:
    """Pushover-only router. play() pushes; log() is silent."""

    def __init__(self, pushover: PushoverClient):
        self.pushover = pushover

    # ── PLAY: actionable-play push (the only thing that reaches the phone) ──

    def play(self, alert: dict | None = None, *, title: str, body: str) -> None:
        """Push a priority-1 notification; persists `alert` (if given) for the deep link."""
        url = None
        if alert is not None:
            try:
                alert_id = alert_store.save_alert(alert)
                if alert_id:
                    url, _ = _build_alert_url(alert_id)
            except Exception as e:
                logger.error(f"Notifier.play: alert_store.save_alert failed: {e}")
        try:
            self.pushover.send(title=title, message=body, url=url, priority=PLAY_PRIORITY)
        except Exception as e:
            logger.error(f"Notifier.play: pushover send failed: {e}")

    # ── LOG: record only, NO push ──────────────────────────────────────────

    def log(self, message_or_alert) -> None:
        if isinstance(message_or_alert, dict):
            try:
                alert_store.save_alert(message_or_alert)
            except Exception as e:
                logger.error(f"Notifier.log: alert_store.save_alert failed: {e}")
            logger.info(f"notify(log) alert: {message_or_alert.get('ticker','?')} "
                        f"{message_or_alert.get('strategy','')} tier={message_or_alert.get('tier','')}")
        else:
            logger.info(f"notify(log): {str(message_or_alert)[:200]}")

    # ── Legacy aliases — now SILENT (route to log, never push) ──────────────

    def alert(self, alert: dict, discord_message: str = "") -> None:
        self.log(alert)

    def message(self, raw_message: str) -> None:
        self.log(raw_message)
