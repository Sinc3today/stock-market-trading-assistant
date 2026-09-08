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
from learning.forward_test import ForwardSpec, ForwardTest

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


# Explicit per-bucket exits. These are the values TIME_EXIT_FRAC produced —
# round(30 * 21/45) = 14 and round(45 * 21/45) = 21 — written out rather than
# computed, because proportional scaling of a time rule is invalid (theta is
# convex in DTE) and it cost the 7DTE book ~$28/trade. Behaviour is unchanged;
# the number is now a decision you can see and argue with.
#
# PENDING (docs/EXIT_TIMING_SWEEP.md): BWB-30DTE fails the OOS era split at
# EVERY exit point, so retuning cannot rescue it — that rung needs a
# keep-or-drop call, not a new close-DTE.
BUCKET_CLOSE_DTE = {"BWB-30DTE": 14, "BWB-45DTE": 21}

SPEC = ForwardSpec(
    name="broken_wing_forward",
    ticker="SPY",
    buckets=BUCKET_CLOSE_DTE,
    target_pct=TARGET_PCT,
    book=config.DIPBUY_FORWARD_BOOK,
    promotion_bar=PROMOTION_BAR,
    enabled_flag="BROKEN_WING_FORWARD_ENABLED",
)
_FT = ForwardTest(SPEC)


def resolve_broken_wing(recorder, *, spy_spot, vix, today=None):
    """Mark + close open BWB candidates at 70% of structural max profit or the
    rung's time stop.

    Delegates to the shared core, which does NOT clamp the close cost — a BWB
    is long a far wing, so being paid to close is legitimate and clamping
    booked phantom max-loss (audit A2).
    """
    return _FT.resolve(recorder, spot=spy_spot, vol=vix, today=today)


def paper_record(recorder) -> dict:
    """Progress vs the promotion bar, NET of commissions, across every BWB
    tenor. This used to score gross while its sibling ladder_forward scored
    net, against a bar that says "net of fees"."""
    from learning.forward_scorecard import net_pnl
    rows = [t for t in recorder.get_all_trades()
            if str(t.get("dte_bucket") or "").startswith(BUCKET_PREFIX)
            and t.get("pnl_dollars") is not None]
    vals = [v for v in (net_pnl(t) for t in rows) if v is not None]
    n = len(vals)
    wins = sum(1 for v in vals if v > 0)
    avg = (sum(vals) / n) if n else 0.0
    return {"n": n, "win_pct": (wins / n * 100) if n else 0.0, "avg": round(avg, 2),
            "bar": PROMOTION_BAR, "by_bucket": _FT.paper_record(recorder),
            "meets_bar": ForwardTest._meets_bar(n, wins, avg)}
