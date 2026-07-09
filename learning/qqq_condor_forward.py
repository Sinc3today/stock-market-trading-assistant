"""learning/qqq_condor_forward.py -- QQQ condor PAPER forward-test (candidate book).

The transfer study (docs/QQQ_CONDOR_TRANSFER.md) said QQQ condors are profitable
in the CURRENT market but bled for 3 years when conditions differed — so they
failed the robustness bar for real money. The user still wants exposure the
disciplined way: paper candidates, zero capital, the market referees.

PROMOTION BAR (set at creation, 2026-07-09, so the goalposts can't move): the
current regime FAVORS this trade, so a green month proves nothing. Promotion
requires the paper record to survive a regime change (a stretch where SPY
condors struggle while QQQ candidates hold up), not just accumulate wins.

Mirrors dipbuy_forward's shape: open on condor-regime days (idempotent/day),
manage at 70%-of-max-profit or 21 DTE. Self-contained per Standing Rule #10.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date as _date

from loguru import logger

import config
from learning.dipbuy_forward import _mark_spread

TARGET_PCT = 0.70
CLOSE_DTE = 21
DTE = 45
BUCKET = "qqq_condor"


def maybe_open_qqq_condor(recorder, *, qqq_spot, vxn, today=None):
    """Record a 1-lot QQQ condor candidate (0.20-delta shorts, $5 wings, ~45DTE
    at VXN sigma). Caller decides WHEN (condor-regime day). Idempotent per day."""
    if not getattr(config, "QQQ_CONDOR_FORWARD_ENABLED", True):
        return None
    if not config.within_entry_window():
        return None
    today = today or _date.today()
    for t in recorder.get_open_trades():
        if t.get("dte_bucket") == BUCKET and \
           str(t.get("entry_date", "")).startswith(today.isoformat()):
            logger.info("qqq_condor_forward: candidate already open today — skip")
            return None
    from signals.condor_calc import build_condor
    c = build_condor(qqq_spot, vxn, dte=DTE)
    if c["credit"] <= 0:
        logger.info("qqq_condor_forward: no credit at current vol — skip")
        return None
    from learning.paper_broker import AUTO_SOURCE
    tid = recorder.log_entry(
        ticker="QQQ",
        entry_price=c["credit"],
        size=1,
        trade_type="iron_condor",
        strategy="iron_condor",
        direction="neutral",
        mode="swing",
        legs=c["legs"],
        max_profit=c["max_profit"],
        max_loss=c["max_loss"],
        notes=f"[CANDIDATE {today.isoformat()}] QQQ condor forward-test "
              f"(promotion bar: survive a regime change — see module docstring)",
        dte_bucket=BUCKET,
        book=config.DIPBUY_FORWARD_BOOK,
        source=AUTO_SOURCE,
    )
    logger.info(f"qqq_condor_forward: recorded candidate {tid} "
                f"(QQQ {qqq_spot:.2f}, credit {c['credit']:.2f})")
    return {"recorded": True, "trade_id": tid}


def resolve_qqq_condors(recorder, *, qqq_spot, vxn, today=None):
    """Mark + close open QQQ condor candidates: 70%-of-max-profit or 21 DTE.
    Credit structure: cost to close = shorts − longs = −_mark_spread."""
    today = today or _date.today()
    trades = recorder.get_all_trades()
    closed = []
    for t in trades:
        if t.get("dte_bucket") != BUCKET or t.get("outcome") not in (None, "open"):
            continue
        legs = t.get("legs") or []
        try:
            expiry = min(_date.fromisoformat(str(l.get("expiry") or l.get("expiration"))[:10])
                         for l in legs if (l.get("expiry") or l.get("expiration")))
        except ValueError:
            continue
        dte_left = (expiry - today).days
        cost = max(0.0, -_mark_spread(legs, qqq_spot, vxn, max(dte_left, 0)))
        pnl = (float(t.get("entry_price", 0)) - cost) * 100 * int(t.get("size", 1))
        mp = t.get("max_profit") or 0.0
        hit_target = mp > 0 and pnl >= TARGET_PCT * mp
        hit_time = dte_left <= CLOSE_DTE
        if hit_target or hit_time:
            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(t["trade_id"], round(cost, 2),
                              notes=f"[CANDIDATE close {today.isoformat()}] {reason} "
                                    f"(QQQ {qqq_spot:.2f})",
                              exit_reason=reason)
            closed.append(t)
    return closed
