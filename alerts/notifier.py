"""
alerts/notifier.py — Unified Notification Router

Routes every alert and message to both:
    PRIMARY   → Pushover  (phone push, short summary + link)
    SECONDARY → Discord   (full card / log message)

Drop-in replacement for the raw Discord functions used in main.py:
    notifier.alert(alert, discord_msg)   replaces  post_alert_sync(alert, msg)
    notifier.message(msg)                replaces  post_message_sync(msg)

Alert persistence:
    Every scanner alert is saved to logs/alerts/<uuid>.json so the
    detail page can load it by ID from the Pushover notification link.
    Full alert data + discord_message are both stored.

Future detail page:
    DASHBOARD_BASE_URL in .env controls the URL embedded in Pushover
    notifications. Point it at your hosted Streamlit or Flask app.
    The alert_id query param is how the page knows which alert to show.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

from alerts.pushover_client import (
    PushoverClient,
    strip_discord_markdown,
    extract_pushover_title,
)

ALERT_LOG_DIR = "logs/alerts"


class Notifier:
    """
    Unified notification router — sends every event to Pushover and Discord.

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
        os.makedirs(ALERT_LOG_DIR, exist_ok=True)

    # ─────────────────────────────────────────
    # ALERT ROUTE  (swing / intraday / SPY options)
    # ─────────────────────────────────────────

    def alert(self, alert: dict, discord_message: str) -> None:
        """
        Route a scanner alert to Pushover (short summary) and Discord (full card).

        Signature matches post_alert_sync(alert, message) so scanners can call
        set_discord_fn(notifier.alert) without any other changes.

        Side-effect: saves alert + full message to logs/alerts/<id>.json for
        the detail page referenced in the Pushover notification URL.
        """
        alert_id         = str(uuid.uuid4())
        alert["alert_id"] = alert_id

        # Persist full payload for the detail page
        self._save_alert(alert, discord_message, alert_id)

        # Pushover — short summary + link
        try:
            self.pushover.send_alert(alert, alert_id)
        except Exception as e:
            logger.error(f"Pushover alert send failed: {e}")

        # Discord — full card (unchanged)
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

        Signature matches post_message_sync(message) so it can be passed
        directly to set_discord_fn() and register_spy_jobs(post_fn=...).

        Pushover receives a stripped, truncated version (≤500 chars).
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

            # Remove the first line from the body if it became the title
            body_lines = body.splitlines()
            if body_lines and body_lines[0].strip() == title.strip():
                body = "\n".join(body_lines[1:]).strip()

            body = body[:500]
            if body:
                self.pushover.send_message(title=title, body=body, priority=-1)
        except Exception as e:
            logger.error(f"Pushover message send failed: {e}")

    # ─────────────────────────────────────────
    # ALERT PERSISTENCE
    # ─────────────────────────────────────────

    def _save_alert(self, alert: dict, discord_message: str, alert_id: str) -> None:
        """
        Write full alert data + Discord message to logs/alerts/<id>.json.

        This is what the detail page loads when you tap the Pushover link.
        The _spy_setup key is excluded (it's a dataclass — not JSON-serializable).
        """
        try:
            payload = {
                k: v for k, v in alert.items() if k != "_spy_setup"
            }
            payload["discord_message"] = discord_message

            path = os.path.join(ALERT_LOG_DIR, f"{alert_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)

            logger.debug(f"Alert saved: {alert_id}")
        except Exception as e:
            logger.error(f"Alert save failed for {alert_id}: {e}")
