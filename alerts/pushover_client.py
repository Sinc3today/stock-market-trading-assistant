"""
alerts/pushover_client.py — Pushover Push Notification Client

Sends real-time push notifications to your phone/devices via the Pushover API.
Acts as the primary alert channel; Discord is secondary (logs + slash commands).

Priority mapping (Pushover scale: -2 silent → 2 emergency):
    high_conviction alerts  →  1  (high, bypasses quiet hours)
    standard alerts         →  0  (normal sound)
    UOA / SPY daily         →  0  (normal sound)
    informational messages  → -1  (low, no sound)

Pushover limits:
    Message body:  1,024 characters (enforced — we send short summaries)
    Title:           250 characters
    URL title:       100 characters
    Requests:      10,000 / month on free plan

Requires in .env:
    PUSHOVER_USER_KEY   = your Pushover user key  (from pushover.net → Your Account)
    PUSHOVER_API_TOKEN  = your app API token       (from pushover.net → Create Application)
    PUSHOVER_BASE_URL   = https://alerts.example   (public host that fronts the
                                                    per-alert FastAPI app via
                                                    Cloudflare Tunnel)
"""

from __future__ import annotations

import re
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests
from loguru import logger

import config


PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

# Priority per alert tier
PRIORITY_MAP: dict[str, int] = {
    "high_conviction": 1,
    "standard":        0,
    "uoa":             0,
    "spy_daily":       0,
    "info":           -1,
}


# ─────────────────────────────────────────
# CLIENT
# ─────────────────────────────────────────

class PushoverClient:
    """
    Thin wrapper around the Pushover REST API.
    Sends push notifications to all registered devices.
    """

    def __init__(self):
        self.token    = config.PUSHOVER_API_TOKEN
        self.user_key = config.PUSHOVER_USER_KEY
        self.enabled  = bool(self.token and self.user_key)
        if not self.enabled:
            logger.warning(
                "Pushover not configured — add PUSHOVER_API_TOKEN and "
                "PUSHOVER_USER_KEY to your .env file"
            )
        else:
            logger.info("PushoverClient initialized ✅")

    # ─────────────────────────────────────────
    # PUBLIC SEND METHODS
    # ─────────────────────────────────────────

    def send(
        self,
        title:     str,
        message:   str,
        url:       str | None = None,
        url_title: str | None = None,
        priority:  int        = 0,
    ) -> bool:
        """
        Send a raw Pushover notification.

        Args:
            title:     Notification title (250 char limit).
            message:   Body text (1,024 char limit).
            url:       Optional URL attached to the notification.
            url_title: Link label (max 100 chars).
            priority:  -1 low | 0 normal | 1 high | 2 emergency.

        Returns:
            True on success, False on any failure.
        """
        if not self.enabled:
            return False

        payload: dict = {
            "token":    self.token,
            "user":     self.user_key,
            "title":    title[:250],
            "message":  message[:1024],
            "priority": priority,
        }
        if url:
            payload["url"]       = url
            payload["url_title"] = (url_title or "View Details")[:100]

        try:
            resp = requests.post(PUSHOVER_API_URL, data=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug(f"Pushover sent: {title!r}")
                return True
            logger.error(
                f"Pushover HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return False
        except requests.Timeout:
            logger.error("Pushover request timed out")
            return False
        except Exception as e:
            logger.error(f"Pushover request failed: {e}")
            return False

    def send_alert(self, alert: dict, alert_id: str | None = None) -> bool:
        """
        Send a short summary of a scanner alert (swing, intraday, SPY options).
        Full card always goes to Discord; this is the phone nudge.
        """
        title    = _build_alert_title(alert)
        body     = _build_alert_body(alert)
        priority = PRIORITY_MAP.get(alert.get("tier", "standard"), 0)
        url, url_title = _build_alert_url(alert_id)
        return self.send(title, body, url=url, url_title=url_title, priority=priority)

    def send_message(
        self,
        title:    str,
        body:     str,
        priority: int = -1,
        url:      str | None = None,
    ) -> bool:
        """
        Send a plain informational push (briefings, SPY daily play, reflection).
        Defaults to low priority (no sound) — these aren't time-critical alerts.
        """
        return self.send(title, body, priority=priority, url=url)


# ─────────────────────────────────────────
# SUMMARY FORMATTERS
# ─────────────────────────────────────────

def _build_alert_title(alert: dict) -> str:
    """Build a short Pushover title from an alert dict."""
    ticker    = alert.get("ticker", "?")
    direction = alert.get("direction", "").upper()
    mode      = alert.get("mode", "").upper()
    tier      = alert.get("tier", "standard")
    strategy  = alert.get("strategy", "")

    tier_tag  = " [HIGH]" if tier == "high_conviction" else ""
    dir_emoji = "📈" if "BULL" in direction else ("📉" if "BEAR" in direction else "📊")

    if strategy:
        label = strategy.replace("_", " ").upper()
        return f"{dir_emoji} SPY — {label}{tier_tag}"

    return f"{dir_emoji} {ticker} — {direction} {mode}{tier_tag}"


def _build_alert_body(alert: dict) -> str:
    """Build a ≤400-char Pushover body from an alert dict."""
    score = alert.get("final_score", 0)

    # ── SPY options setup ───────────────────────────────────────
    spy_setup = alert.get("_spy_setup")
    if spy_setup is not None:
        lines = [f"Score: {score}/100"]
        strategy = getattr(spy_setup, "strategy", "")

        if strategy == "iron_condor":
            pl = getattr(spy_setup, "ic_put_long",   None)
            ps = getattr(spy_setup, "ic_put_short",  None)
            cs = getattr(spy_setup, "ic_call_short", None)
            cl = getattr(spy_setup, "ic_call_long",  None)
            cr = getattr(spy_setup, "ic_credit",     None)
            if all(v is not None for v in [pl, ps, cs, cl]):
                lines.append(
                    f"${pl:.0f}p / ${ps:.0f}p / ${cs:.0f}c / ${cl:.0f}c"
                )
            if cr is not None:
                lines.append(f"~${cr:.2f} credit per side")

        elif strategy in ("call_debit_spread", "put_debit_spread"):
            ls = getattr(spy_setup, "long_strike",  None)
            ss = getattr(spy_setup, "short_strike", None)
            ed = getattr(spy_setup, "est_debit",    None)
            rr = getattr(spy_setup, "spread_rr",    None)
            opt = "CALL" if strategy == "call_debit_spread" else "PUT"
            if ls is not None and ss is not None:
                lines.append(f"Buy {opt} ${ls:.0f} / Sell ${ss:.0f}")
            if ed is not None:
                rr_str = f" | R/R {rr:.1f}:1" if rr else ""
                lines.append(f"~${ed:.2f} debit{rr_str}")

        reasons = getattr(spy_setup, "reasons", [])
        if reasons:
            clean = [r.replace("✅ ", "").replace("⚠️ ", "") for r in reasons[:2]]
            lines.append(" · ".join(clean))

        return "\n".join(lines)[:400]

    # ── Standard ticker alert ───────────────────────────────────
    entry  = alert.get("entry",    0)
    stop   = alert.get("stop",     0)
    target = alert.get("target",   0)
    rr     = alert.get("rr_ratio", 0)
    tags   = alert.get("setup_tags", [])
    rsi    = alert.get("rsi")
    rvol   = alert.get("rvol")

    lines = [f"Score: {score}/100 | R/R: {rr}:1"]
    if entry:
        lines.append(f"Entry ${entry} → Stop ${stop} → Target ${target}")
    if tags:
        clean = [t.replace("✅ ", "").replace("⚠️ ", "") for t in tags[:3]]
        lines.append(" · ".join(clean))
    if rsi or rvol:
        extras = []
        if rsi:
            extras.append(f"RSI {rsi:.0f}" if isinstance(rsi, float) else f"RSI {rsi}")
        if rvol:
            extras.append(f"RVOL {rvol:.1f}x" if isinstance(rvol, float) else f"RVOL {rvol}x")
        lines.append("  ".join(extras))

    return "\n".join(lines)[:400]


def _build_alert_url(alert_id: str | None) -> tuple[str | None, str | None]:
    """Build the FastAPI detail-page URL for a Pushover notification."""
    if not alert_id:
        return None, None
    base = (getattr(config, "PUSHOVER_BASE_URL", "") or "").rstrip("/")
    if not base:
        return None, None
    return f"{base}/alerts/{alert_id}", "View Trade + Chat"


# ─────────────────────────────────────────
# MARKDOWN STRIPPER (Discord → plain text)
# ─────────────────────────────────────────

def strip_discord_markdown(text: str) -> str:
    """
    Convert Discord-formatted markdown to plain text suitable for Pushover.
    Removes bold, italic, underline, inline code, and box-drawing characters.
    """
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)   # **bold**
    text = re.sub(r'\*(.*?)\*',     r'\1', text)   # *italic*
    text = re.sub(r'__(.*?)__',     r'\1', text)   # __underline__
    text = re.sub(r'_([^_\n]+)_',  r'\1', text)   # _italic_
    text = re.sub(r'`(.*?)`',       r'\1', text)   # `code`
    text = re.sub(r'━+',            '─',   text)   # box-drawing → simple dash
    text = re.sub(r'[ \t]+',        ' ',   text)   # collapse spaces
    return text.strip()


def extract_pushover_title(message: str) -> str:
    """
    Pull the first meaningful line from a Discord message to use as a Pushover title.
    Strips markdown and ignores separator lines.
    """
    for line in message.splitlines():
        line = line.strip()
        if not line or re.match(r'^[━─\-=]+$', line):
            continue
        clean = strip_discord_markdown(line)
        if clean:
            return clean[:100]
    return "Trading Assistant"
