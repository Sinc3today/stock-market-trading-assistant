"""learning/broken_wing_forward.py -- broken-wing butterfly PAPER forward-test.

The broken-wing study (docs/BROKEN_WING_STUDY.md, 2026-07-18) found a put BWB
(0.35-delta body, 3/8 wings) BEATS the plain condor in trending_up_calm at 30
and 45 DTE and survived the full gauntlet — per-regime, OOS era split, 10% fill
haircut, AND a parameter-robustness sweep (45DTE 16/16 combos pass, 30DTE 15/16).
The first directional-lean structure to earn a live look within defined risk.

PROMOTION BAR (set at creation so the goalposts can't move):
  >= 15 closed paper trades AND win rate >= 70% AND avg P&L > $20/trade AND
  no single loss beyond ~max_loss (structure integrity). On promotion it follows
  the 7DTE/1-3DTE path: disciplined book + the approve alert for live mirroring.

Mirrors seven_dte_forward's shape (Standing Rule #10: self-contained). Opens on
trending_up_calm days only (caller decides WHEN), 1-lot per tenor, idempotent per
day; managed at 70% of structural max profit OR the ladder time-exit
(round(dte * 21/45)). Unlike a pure short-premium condor, a BWB can be worth
money to close (long far wing), so P&L uses the signed 'broken_wing' convention
in TradeRecorder — do NOT clamp the close cost to >= 0.
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
TIME_EXIT_FRAC = 21 / 45          # live parity, same as the DTE ladder
BUCKET_PREFIX = "BWB"
PROMOTION_BAR = "n>=15 closed, win>=70%, avg>$20, no loss>max_loss"


def _today_et() -> _date:
    return _datetime.now(pytz.timezone("US/Eastern")).date()


def _bucket_for(dte: int) -> str:
    return f"{BUCKET_PREFIX}-{dte}DTE"


def _dte_from_bucket(bucket: str) -> int | None:
    """'BWB-45DTE' -> 45."""
    try:
        return int(str(bucket).split("-")[1].replace("DTE", ""))
    except (IndexError, ValueError):
        return None


def maybe_open_broken_wing(recorder, *, spy_spot, vix, today=None):
    """Record a 1-lot SPY broken-wing butterfly candidate at each configured
    tenor. Caller decides WHEN (a trending_up_calm, tradeable day). Idempotent
    per (tenor, day). Returns the list of opened trade ids."""
    if not getattr(config, "BROKEN_WING_FORWARD_ENABLED", True):
        return []
    if not config.within_entry_window():
        return []
    today = today or _today_et()
    from signals.condor_calc import build_broken_wing
    from learning.paper_broker import AUTO_SOURCE

    open_trades = recorder.get_open_trades()
    opened = []
    for dte in getattr(config, "BROKEN_WING_FORWARD_DTES", (30, 45)):
        bucket = _bucket_for(dte)
        if any(t.get("dte_bucket") == bucket
               and str(t.get("entry_date", "")).startswith(today.isoformat())
               for t in open_trades):
            logger.info(f"broken_wing_forward: {bucket} already open today — skip")
            continue
        c = build_broken_wing(spy_spot, vix, dte=dte, today=today)
        if c is None:
            logger.info(f"broken_wing_forward: no {bucket} structure at spot — skip")
            continue
        tid = recorder.log_entry(
            ticker="SPY",
            entry_price=c["credit"],          # net credit received (signed)
            size=1,
            trade_type="broken_wing",
            strategy="broken_wing",
            direction="neutral",
            mode="swing",
            legs=c["legs"],
            max_profit=c["max_profit"],
            max_loss=c["max_loss"],
            notes=f"[CANDIDATE {today.isoformat()}] {dte}DTE broken-wing butterfly "
                  f"forward-test (0.35Δ/3-8 wings; promotion bar: {PROMOTION_BAR})",
            dte_bucket=bucket,
            book=config.DIPBUY_FORWARD_BOOK,
            source=AUTO_SOURCE,
        )
        logger.info(f"broken_wing_forward: recorded {bucket} {tid} "
                    f"(SPY {spy_spot:.2f}, credit {c['credit']:.2f})")
        opened.append(tid)
    return opened


def resolve_broken_wing(recorder, *, spy_spot, vix, today=None):
    """Mark + close open BWB candidates: 70% of structural max profit OR the
    ladder time-exit (round(dte * 21/45))."""
    today = today or _today_et()
    closed = []
    for t in recorder.get_all_trades():
        bucket = t.get("dte_bucket") or ""
        if not str(bucket).startswith(BUCKET_PREFIX):
            continue
        if t.get("outcome") not in (None, "open"):
            continue
        if t.get("book") != config.DIPBUY_FORWARD_BOOK:
            continue          # promoted/live BWBs are the exit manager's job
        legs = t.get("legs") or []
        try:
            expiry = min(_date.fromisoformat(str(l.get("expiry") or l.get("expiration"))[:10])
                         for l in legs if (l.get("expiry") or l.get("expiration")))
        except ValueError:
            continue
        dte_left = (expiry - today).days
        # Signed close cost: a BWB can be worth money to close (long far wing),
        # so cost may be negative — do NOT clamp it (that would zero real gains).
        cost = -_mark_spread(legs, spy_spot, vix, max(dte_left, 0))
        entry_credit = float(t.get("entry_price", 0))
        pnl = (entry_credit - cost) * 100 * int(t.get("size", 1))
        mp = t.get("max_profit") or 0.0
        orig_dte = _dte_from_bucket(bucket) or 45
        close_dte = max(1, round(orig_dte * TIME_EXIT_FRAC))
        hit_target = mp > 0 and pnl >= TARGET_PCT * mp
        hit_time = dte_left <= close_dte
        if hit_target or hit_time:
            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(t["trade_id"], round(cost, 2),
                              notes=f"[CANDIDATE close {today.isoformat()}] {reason} "
                                    f"(SPY {spy_spot:.2f})",
                              exit_reason=reason)
            closed.append(t)
    return closed


def paper_record(recorder) -> dict:
    """Progress vs the promotion bar — surfaced by loop_health / the playbook.
    Aggregates every BWB tenor."""
    closed = [t for t in recorder.get_all_trades()
              if str(t.get("dte_bucket") or "").startswith(BUCKET_PREFIX)
              and t.get("pnl_dollars") is not None]
    n = len(closed)
    wins = sum(1 for t in closed if (t.get("pnl_dollars") or 0) > 0)
    avg = (sum(float(t.get("pnl_dollars") or 0) for t in closed) / n) if n else 0.0
    return {"n": n, "win_pct": (wins / n * 100) if n else 0.0, "avg": avg,
            "bar": PROMOTION_BAR,
            "meets_bar": n >= 15 and (wins / n) >= 0.70 and avg > 20.0}
