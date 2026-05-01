"""
alerts/notifier.py -- Unified Notification Router

Routes every alert and message to both:
    PRIMARY   -> Pushover  (phone push, short summary + deep link)
    SECONDARY -> Discord   (full card / log message)

Drop-in replacement for the raw Discord functions used in main.py:
    notifier.alert(alert, discord_msg)   replaces  post_alert_sync(alert, msg)
    notifier.message(msg)                replaces  post_message_sync(msg)

Alert persistence:
    Every scanner alert is saved to the SQLite store at logs/alert_store.db
    so the per-alert web app can load it by ID. The Pushover notification
    URL embeds that same ID (config.PUSHOVER_BASE_URL/alerts/<id>).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

from alerts import alert_store
from alerts.pushover_client import (
    PushoverClient,
    strip_discord_markdown,
    extract_pushover_title,
)


class Notifier:
    """
    Unified notification router -- sends every event to Pushover and Discord.

    Usage in main.py:
        notifier = Notifier(pushover, post_alert_sync, post_message_sync)
        swing_scanner.set_discord_fn(notifier.alert)
        options_flow_scanner.set_discord_fn(notifier.message)
    """

    def __init__(
        self,
        pushover:           PushoverClient,
        discord_alert_fn,   # post_alert_sync(alert, message)
        discord_message_fn, # post_message_sync(message)
    ):
        self.pushover          = pushover
        self._discord_alert    = discord_alert_fn
        self._discord_message  = discord_message_fn

    # ─────────────────────────────────────────
    # ALERT ROUTE  (swing / intraday / SPY options)
    # ─────────────────────────────────────────

    def alert(self, alert: dict, discord_message: str) -> None:
        """
        Route a scanner alert to Pushover (short summary + deep link)
        and Discord (full card).

        Side-effect: persists the alert to alert_store.db before sending,
        so the link embedded in the Pushover notification resolves to a
        real per-alert page in the web app.
        """
        # 1. Persist first — gives us the alert_id used in the deep link.
        alert.setdefault("discord_message", discord_message)
        try:
            alert_id = alert_store.save_alert(alert)
        except Exception as e:
            logger.error(f"alert_store.save_alert failed: {e}")
            alert_id = None
        alert["alert_id"] = alert_id

        # 2. Pushover — short summary + deep link to the alert page.
        try:
            self.pushover.send_alert(alert, alert_id)
        except Exception as e:
            logger.error(f"Pushover alert send failed: {e}")

        # 3. Discord — full card (unchanged).
        if self._discord_alert:
            try:
                self._discord_alert(alert, discord_message)
            except Exception as e:
                logger.error(f"Discord alert send failed: {e}")

    # ─────────────────────────────────────────
    # MESSAGE ROUTE  (briefings / UOA / SPY daily / reflection)
    # ─────────────────────────────────────────

    def message(self, raw_message: str) -> None:
        """
        Route a plain-text (or Discord-markdown) message to both channels.

        Pushover receives a stripped, truncated version (<= 500 chars);
        Discord receives the full message unchanged.
        """
        # Discord — full message
        if self._discord_message:
            try:
                self._discord_message(raw_message)
            except Exception as e:
                logger.error(f"Discord message send failed: {e}")

        # Pushover — strip markdown, extract title from first line
        try:
            title = extract_pushover_title(raw_message)
            body  = strip_discord_markdown(raw_message)

            body_lines = body.splitlines()
            if body_lines and body_lines[0].strip() == title.strip():
                body = "\n".join(body_lines[1:]).strip()

            body = body[:500]
            if body:
                self.pushover.send_message(title=title, body=body, priority=-1)
        except Exception as e:
            logger.error(f"Pushover message send failed: {e}")
