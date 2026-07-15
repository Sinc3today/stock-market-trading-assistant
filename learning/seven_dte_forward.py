"""learning/seven_dte_forward.py -- 7DTE SPY condor PAPER forward-test.

The DTE-ladder study (docs/DTE_LADDER_STUDY.md, 2026-07-15) found the 7DTE
condor is the best undeployed rung in the project: 82% win / $33.50 per trade
in choppy_low_vol UNDER a 10% fill haircut, positive in both eras. Same
physics as the validated 1-3DTE condor: fast theta capture is robust to fill
friction.

PROMOTION BAR (set at creation so the goalposts can't move):
  >= 15 closed paper trades AND win rate >= 70% AND avg P&L > $20/trade AND
  no single loss beyond ~max_loss (structure integrity). Expected ~4-6 weeks
  in condor regimes. On promotion it follows the 1-3DTE path: disciplined
  book + the can't-miss approve alert for live mirroring.

Mirrors qqq_condor_forward's shape (Standing Rule #10: self-contained).
Opens on condor-regime days, 1-lot, idempotent per day; managed at 70% of
max profit or 3 DTE (the ladder's scaled time exit: round(7 * 21/45) = 3).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date as _date, datetime as _datetime

import pytz
from loguru import logger

import config
from learning.dipbuy_forward import _mark_spread

TARGET_PCT = 0.70
CLOSE_DTE = 3          # round(7 * 21/45) — live-parity fraction from the study
DTE = 7
BUCKET = "7DTE"
PROMOTION_BAR = "n>=15 closed, win>=70%, avg>$20, no loss>max_loss"


def _today_et() -> _date:
    return _datetime.now(pytz.timezone("US/Eastern")).date()


def maybe_open_seven_dte(recorder, *, spy_spot, vix, today=None):
    """Record a 1-lot SPY 7DTE condor candidate (0.20-delta shorts, $5 wings).
    Caller decides WHEN (condor-regime day). Idempotent per day."""
    if not getattr(config, "SEVEN_DTE_FORWARD_ENABLED", True):
        return None
    if not config.within_entry_window():
        return None
    today = today or _today_et()
    for t in recorder.get_open_trades():
        if t.get("dte_bucket") == BUCKET and \
           str(t.get("entry_date", "")).startswith(today.isoformat()):
            logger.info("seven_dte_forward: candidate already open today — skip")
            return None
    from signals.condor_calc import build_condor
    c = build_condor(spy_spot, vix, dte=DTE)
    if c["credit"] <= 0:
        logger.info("seven_dte_forward: no credit at current vol — skip")
        return None
    from learning.paper_broker import AUTO_SOURCE
    tid = recorder.log_entry(
        ticker="SPY",
        entry_price=c["credit"],
        size=1,
        trade_type="iron_condor",
        strategy="iron_condor",
        direction="neutral",
        mode="swing",
        legs=c["legs"],
        max_profit=c["max_profit"],
        max_loss=c["max_loss"],
        notes=f"[CANDIDATE {today.isoformat()}] 7DTE condor forward-test "
              f"(promotion bar: {PROMOTION_BAR})",
        dte_bucket=BUCKET,
        book=config.DIPBUY_FORWARD_BOOK,
        source=AUTO_SOURCE,
    )
    logger.info(f"seven_dte_forward: recorded candidate {tid} "
                f"(SPY {spy_spot:.2f}, credit {c['credit']:.2f})")
    return {"recorded": True, "trade_id": tid}


def resolve_seven_dte(recorder, *, spy_spot, vix, today=None):
    """Mark + close open 7DTE candidates: 70%-of-max-profit or 3 DTE."""
    today = today or _today_et()
    closed = []
    for t in recorder.get_all_trades():
        if t.get("dte_bucket") != BUCKET or t.get("outcome") not in (None, "open"):
            continue
        if t.get("book") != config.DIPBUY_FORWARD_BOOK:
            continue          # promoted/live 7DTE positions are the exit manager's job
        legs = t.get("legs") or []
        try:
            expiry = min(_date.fromisoformat(str(l.get("expiry") or l.get("expiration"))[:10])
                         for l in legs if (l.get("expiry") or l.get("expiration")))
        except ValueError:
            continue
        dte_left = (expiry - today).days
        cost = max(0.0, -_mark_spread(legs, spy_spot, vix, max(dte_left, 0)))
        pnl = (float(t.get("entry_price", 0)) - cost) * 100 * int(t.get("size", 1))
        mp = t.get("max_profit") or 0.0
        hit_target = mp > 0 and pnl >= TARGET_PCT * mp
        hit_time = dte_left <= CLOSE_DTE
        if hit_target or hit_time:
            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(t["trade_id"], round(cost, 2),
                              notes=f"[CANDIDATE close {today.isoformat()}] {reason} "
                                    f"(SPY {spy_spot:.2f})",
                              exit_reason=reason)
            closed.append(t)
    return closed


def paper_record(recorder) -> dict:
    """Progress vs the promotion bar — surfaced by loop_health / the playbook."""
    closed = [t for t in recorder.get_all_trades()
              if t.get("dte_bucket") == BUCKET
              and t.get("pnl_dollars") is not None]
    n = len(closed)
    wins = sum(1 for t in closed if (t.get("pnl_dollars") or 0) > 0)
    avg = (sum(float(t.get("pnl_dollars") or 0) for t in closed) / n) if n else 0.0
    return {"n": n, "win_pct": (wins / n * 100) if n else 0.0, "avg": avg,
            "bar": PROMOTION_BAR,
            "meets_bar": n >= 15 and (wins / n) >= 0.70 and avg > 20.0}
