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
from learning.forward_test import ForwardSpec, ForwardTest

TARGET_PCT = 0.70
# 2026-09-06: was 3, derived as round(7 * 21/45) — the 45DTE time-stop scaled
# proportionally. That heuristic is wrong, because theta is NOT linear in DTE:
# it accelerates into expiry, so the last days hold most of the decay. Closing
# a 7DTE condor at 3 DTE surrenders the best part of the trade while having
# already borne the risk of the first four days.
#
# Sweep (calm regime, OOS era-split, 10% haircut + commissions —
# docs/SEVEN_DTE_STRUCTURE_STUDY.md):
#     hold to expiry  86% win  +$59.11  PASS
#     close at 1 DTE  80% win  +$39.43  PASS
#     close at 2 DTE  78% win  +$25.31  PASS
#     close at 3 DTE  71% win  +$11.16  PASS      <- what we were doing
#     close at 4 DTE  62% win   -$0.42  fail-OOS
#
# 1 rather than 0: holding into expiry risks assignment on an ITM short, and
# 1 DTE keeps most of the edge (+$39 of the +$59) while still passing OOS.
CLOSE_DTE = 1
DTE = 7
BUCKET = "7DTE"
PROMOTION_BAR = "n>=15 closed, win>=70%, avg>$20, no loss>max_loss"
# The 8 trades closed before 2026-09-06 ran the CLOSE_DTE=3 rule and are a
# DIFFERENT strategy. Pooling across a rule change is exactly the error the
# audit flagged as E2, so the promotion count starts again here.
RULE_EPOCH = "2026-09-06"


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


SPEC = ForwardSpec(
    name="seven_dte_forward",
    ticker="SPY",
    buckets={BUCKET: CLOSE_DTE},
    target_pct=TARGET_PCT,
    book=config.DIPBUY_FORWARD_BOOK,
    promotion_bar=PROMOTION_BAR,
    rule_epoch=RULE_EPOCH,
    enabled_flag="SEVEN_DTE_FORWARD_ENABLED",
)
_FT = ForwardTest(SPEC)


def resolve_seven_dte(recorder, *, spy_spot, vix, today=None):
    """Mark + close open 7DTE candidates at 70% of max profit or CLOSE_DTE.

    Delegates to the shared core so a fix lands on every rung at once — this
    body used to be a near-copy of three siblings, and every defect the
    2026-09-07 audit found existed in some but not all of them.
    """
    return _FT.resolve(recorder, spot=spy_spot, vol=vix, today=today)


def paper_record(recorder) -> dict:
    """Progress vs the promotion bar — surfaced by loop_health / the playbook.

    NET of commissions (the bar says so; this used to score gross), and counts
    only trades opened under the CURRENT exit rule. Pre-epoch trades ran
    CLOSE_DTE=3 and are reported separately as `legacy`, because a record
    pooled across a rule change describes a strategy nobody is running
    (docs/FORWARD_TEST_AUDIT.md E2).
    """
    rec = _FT.paper_record(recorder)[BUCKET]
    rec["legacy"]["rule"] = "CLOSE_DTE=3"
    return rec
