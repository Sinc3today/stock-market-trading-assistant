"""alerts/entry_approve.py -- entry-approve emergency alert (trade-copilot).

The user said normal Pushover pings get lost in a noisy alert stream and they
"miss the window to make the trade." So when a tradeable daily play opens at
09:45 (in-window by construction), fire an EMERGENCY Pushover (priority 2 —
re-alerts until acked, distinct sound) with the RH-shaped legs and a one-tap
Tailscale link straight to /copilot, where they mirror it and tap "I placed it."

Emergency (not high) because approving an entry IS a trade action — the same bar
as the stop watchdog. The builder is pure; the send is a thin wrapper.
"""
from __future__ import annotations

from loguru import logger

from alerts.stop_watchdog import rh_leg_lines


def _expiry(trade: dict) -> str:
    from alerts.fmt import fmt_date
    for leg in (trade.get("legs") or []):
        e = leg.get("expiry") or leg.get("expiration")
        if e:
            return fmt_date(str(e)[:10])   # house style: MM-DD-YY
    return trade.get("dte_bucket") or "—"


def entry_day_note(trade: dict, today=None) -> str:
    """Entry-day quality tag for SHORT-DTE condors (docs/DOW_STUDY.md, 5yr,
    pessimistic sim): Fri entries harvest 3 days of weekend theta for 1 day of
    market risk (81% win), Mon rides the strongest intraday day (81%), Wed is
    the weakest slot (67%, $26 avg). Informational — never a gate; the user
    sizes with it, the bot doesn't filter on it."""
    from datetime import date as _d
    if (trade.get("strategy") or "") != "iron_condor":
        return ""
    if trade.get("dte_bucket") not in ("0DTE", "1-3DTE"):
        return ""
    dow = (today or _d.today()).weekday()
    if dow == 4:
        return "🗓 Fri entry — weekend theta ✓ (best slot, 81% hist.)"
    if dow == 0:
        return "🗓 Mon — strong entry day (81% hist.)"
    if dow == 2:
        return "🗓 Wed — weakest entry day (67% hist.) — consider small size"
    return ""


def build_approve_alert(trade: dict, base_url: str | None, today=None) -> dict:
    """Build the entry-approve Pushover payload (title/body/url/url_title) from a
    recorded trade dict. Pure — no I/O."""
    strat = (trade.get("strategy") or trade.get("trade_type") or "play").replace("_", " ")
    ticker = trade.get("ticker", "SPY")
    legs = rh_leg_lines(trade.get("legs") or [])

    lines = [f"{ticker} {strat}"]
    if legs:
        lines.extend(legs)
    exp = _expiry(trade)
    entry = trade.get("entry_price")
    tail = f"Exp {exp}"
    if entry is not None:
        tail += f"  ·  net {float(entry):g}"
    lines.append(tail)
    note = entry_day_note(trade, today)
    if note:
        lines.append(note)
    lines.append("Open Copilot to place it on Robinhood.")

    url = f"{base_url.rstrip('/')}/copilot" if base_url else None
    return {
        "title": f"🟢 Approve: {ticker} {strat}",
        "body": "\n".join(lines),
        "url": url,
        "url_title": "Open Copilot → place it",
    }


def notify_entry_approve(trade: dict, pushover, base_url: str | None = None) -> bool:
    """Send the entry-approve emergency Pushover. Returns the client's result
    (or False on error). base_url defaults to config.PUSHOVER_BASE_URL."""
    if base_url is None:
        try:
            import config
            base_url = getattr(config, "PUSHOVER_BASE_URL", None)
        except Exception:
            base_url = None
    a = build_approve_alert(trade, base_url)
    logger.info(f"entry_approve: {a['title']} -> {a['url']}")
    try:
        return bool(pushover.send(
            title=a["title"], message=a["body"],
            url=a["url"], url_title=a["url_title"], priority=2))
    except Exception as e:
        logger.error(f"entry_approve send failed: {e}")
        return False
