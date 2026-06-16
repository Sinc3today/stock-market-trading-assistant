"""alerts/stop_watchdog.py -- smart-stop watchdog (trade-copilot core).

The thing Robinhood can't do: a stop keyed off the UNDERLYING (SPY vs the short
strikes), not the option mark. RH stops on a condor trip on bid/ask blips and
close instantly; this watches where SPY actually is and fires a can't-miss
EMERGENCY Pushover as SPY approaches a short strike, so you close on RH yourself
before max loss. Take-profits stay as RH limit orders — the bot owns the stop side.

Runs intraday (every scan) over the open positions in the journal. Dedupes so each
position alerts once per day (emergency priority re-alerts until you ack anyway).
"""
from __future__ import annotations

from loguru import logger


def short_strikes(legs):
    """(short_put, short_call) from a position's legs; either may be None."""
    sp = sc = None
    for leg in legs or []:
        action = (leg.get("action") or "").upper()
        typ = (leg.get("option_type") or leg.get("type") or "").upper()
        strike = leg.get("strike")
        if strike is None or not action.startswith("SELL"):
            continue
        if typ.startswith("P"):
            sp = strike
        elif typ.startswith("C"):
            sc = strike
    return sp, sc


def stop_signal(legs, spot: float, buffer_pct: float = 0.005):
    """Underlying-keyed stop. Warns when SPY comes within `buffer_pct` of a short
    strike (a put below or a call above). Returns (triggered, reason)."""
    sp, sc = short_strikes(legs)
    if sp is not None and spot <= sp * (1 + buffer_pct):
        return True, f"SPY ${spot:.2f} at/near SHORT PUT ${sp:g} — close to manage"
    if sc is not None and spot >= sc * (1 - buffer_pct):
        return True, f"SPY ${spot:.2f} at/near SHORT CALL ${sc:g} — close to manage"
    return False, ""


def rh_leg_lines(legs) -> list[str]:
    """Legs as copy-ready Robinhood-shaped lines, e.g. 'SELL $700 PUT'."""
    out = []
    for leg in legs or []:
        action = (leg.get("action") or "").upper()
        typ = (leg.get("option_type") or leg.get("type") or "").upper()
        strike = leg.get("strike")
        if not action or strike is None or not typ:
            continue
        out.append(f"{action} ${strike:g} {typ}")
    return out


def position_status(legs, spot: float, buffer_pct: float = 0.005):
    """3-tier status for the companion screen:
    NEAR STOP (within the stop buffer of a short), WATCH (within 2x buffer),
    else SAFE. Returns (label, css_class)."""
    if stop_signal(legs, spot, buffer_pct)[0]:
        return "NEAR STOP", "status-loss"
    if stop_signal(legs, spot, buffer_pct * 2)[0]:
        return "WATCH", "status-be"
    return "SAFE", "status-win"


def check_open_positions(recorder, spot: float, pushover, alerted: set,
                         buffer_pct: float = 0.005,
                         books=("disciplined", "live")) -> int:
    """For each open position whose underlying is near a short strike, fire one
    emergency Pushover (deduped via `alerted`). Returns the number of new alerts."""
    if spot is None:
        return 0
    n = 0
    for t in recorder.get_open_trades():
        tid = t.get("trade_id")
        if tid in alerted or (t.get("book") or "disciplined") not in books:
            continue
        legs = t.get("legs") or []
        if not legs:
            continue
        trig, reason = stop_signal(legs, spot, buffer_pct)
        if not trig:
            continue
        strat = t.get("strategy", "position")
        logger.warning(f"stop_watchdog: {tid} {strat} stop — {reason}")
        if pushover:
            pushover.send(f"🛑 Close {strat} ({t.get('ticker','SPY')})",
                          f"{reason}\nTrade {tid}. Close it on Robinhood.", priority=2)
        alerted.add(tid)
        n += 1
    return n
