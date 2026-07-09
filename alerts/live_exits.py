"""alerts/live_exits.py -- exit alerts for the LIVE book (real money).

Audit finding (2026-07-08): paper positions auto-close at the 70% profit target /
21-DTE time stop, but the user's REAL positions got no exit signal at all — only
emergencies (stops) fired, so a live condor could sit at 80% of max profit and
decay back unnoticed. This closes that asymmetry: a high-priority (1) Pushover —
good news, not an emergency — when a live position hits its profit target or ages
to the time exit. The user closes manually on RH (take-profit limit orders remain
their primary tool; this is the safety net when a limit didn't fill).

Dedupe: one alert per (trade, reason) per day via the caller-owned `alerted` set.
"""
from __future__ import annotations

from datetime import date, datetime

from loguru import logger

PROFIT_TARGET_PCT = 0.70   # mirror of the paper book's exit rule
DTE_CLOSE_THRESHOLD = 21


def _min_expiry(trade: dict) -> date | None:
    dates = []
    for leg in (trade.get("legs") or []):
        e = leg.get("expiry") or leg.get("expiration")
        if e:
            try:
                dates.append(date.fromisoformat(str(e)[:10]))
            except ValueError:
                continue
    return min(dates) if dates else None


def _default_mtm(trade: dict):
    """Live MTM dollars from real NBBO quotes (yfinance). Raises on failure."""
    from data.market_quotes import fetch_leg_quotes, position_mtm
    strat = (trade.get("strategy") or "").lower()
    action = "debit" if ("debit" in strat or strat == "single_leg") else "credit"
    legs = fetch_leg_quotes(trade.get("ticker", "SPY"), trade.get("legs") or [])
    m = position_mtm(legs, entry_price=trade.get("entry_price") or 0,
                     size=trade.get("size") or 1, action=action)
    if not m:
        raise RuntimeError("no usable quotes")
    return m["mtm_dollars"]


def check_live_exits(recorder, pushover, alerted: set, *, mtm_fn=_default_mtm,
                     profit_target_pct: float = PROFIT_TARGET_PCT,
                     dte_close: int = DTE_CLOSE_THRESHOLD,
                     today: date | None = None) -> int:
    """Alert on live-book positions that hit the profit target or the time exit.
    Returns the number of new alerts. Never raises (quote failures degrade to the
    DTE check, which needs no market data)."""
    today = today or date.today()
    n = 0
    for t in recorder.get_open_trades():
        if t.get("book") != "live" or (t.get("outcome") or "open") != "open":
            continue
        tid = t.get("trade_id")
        strat = (t.get("strategy") or "position").replace("_", " ")

        # ── profit target (needs quotes; best-effort) ──
        key_t = (tid, "target")
        max_profit = t.get("max_profit")
        if key_t not in alerted and isinstance(max_profit, (int, float)) and max_profit > 0:
            try:
                mtm = mtm_fn(t)
                pct = mtm / max_profit
                if pct >= profit_target_pct:
                    msg = (f"{strat} at {pct:.0%} of max profit "
                           f"(+${mtm:,.0f} of ${max_profit:,.0f}). "
                           f"Consider closing on Robinhood — the paper book "
                           f"takes profit at {profit_target_pct:.0%}.")
                    logger.info(f"live_exits: {tid} profit target — {pct:.0%}")
                    if pushover:
                        pushover.send(f"🎯 Take profit: {t.get('ticker','SPY')} {strat}",
                                      msg, priority=1)
                    alerted.add(key_t)
                    n += 1
            except Exception as e:
                logger.warning(f"live_exits: MTM failed for {tid}: {e}")

        # ── time exit (no market data needed) ──
        key_d = (tid, "dte")
        exp = _min_expiry(t)
        if key_d not in alerted and exp is not None:
            dte = (exp - today).days
            if dte <= dte_close:
                msg = (f"{strat} is {dte} days from expiry ({exp.isoformat()}). "
                       f"The paper book closes at {dte_close} DTE — gamma risk "
                       f"grows from here. Consider closing on Robinhood.")
                logger.info(f"live_exits: {tid} time exit — {dte} DTE")
                if pushover:
                    pushover.send(f"⏳ {dte} DTE: {t.get('ticker','SPY')} {strat}",
                                  msg, priority=1)
                alerted.add(key_d)
                n += 1
    return n
