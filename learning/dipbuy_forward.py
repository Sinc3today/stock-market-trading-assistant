"""learning/dipbuy_forward.py -- LIVE paper-only forward test of the oversold
dip-buy candidate (docs/DIPBUY_STUDY.md).

Records a 1-contract bull-call debit on each fresh RSI<30 cross into the
'candidate' book (excluded from headline /trades stats) and manages it daily
(50%-of-max-profit OR 10-trading-day hold) to confirm/kill the in-sample edge
on unseen data. Self-contained: the core ExitManager is NOT touched. Both the
entry hook and the resolver are wrapped by their callers per Standing Rule #10.
Spec: docs/superpowers/specs/2026-06-07-dipbuy-forward-test-design.md
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date as _date

from loguru import logger

import config
from backtests.dipbuy_signal_study import rsi_series, oversold_triggers


def is_fresh_oversold(spy_df) -> bool:
    """True iff the latest daily bar is a FRESH RSI(14)<30 cross (today<30,
    yesterday>=30) — same trigger the dip-buy study validated."""
    if spy_df is None or len(spy_df) < 30:
        return False
    trig = oversold_triggers(rsi_series(spy_df["close"].astype(float), 14), 30.0)
    return bool(trig.iloc[-1])


def maybe_open_dipbuy(spy_df, *, spot, ivr, options_layer, recorder, today=None):
    """On a fresh oversold cross, build a bull-call debit (~21 DTE) via the same
    OptionsLayer the live bull plays use and record a 1-ct paper trade in the
    'candidate' book. Idempotent per day. Returns {'recorded': True,
    'trade_id': tid} or None."""
    if not config.DIPBUY_FORWARD_ENABLED:
        return None
    if not is_fresh_oversold(spy_df):
        return None
    today = today or _date.today()
    # idempotency: skip if a candidate trade already opened today
    for t in recorder.get_open_trades():
        if t.get("book") == config.DIPBUY_FORWARD_BOOK and \
           str(t.get("entry_date", "")).startswith(today.isoformat()):
            logger.info("dipbuy_forward: candidate already open today — skip")
            return None

    score_result = {"final_score": 85, "direction": "bullish", "tier": "dipbuy"}
    target, stop = round(spot * 1.03, 2), round(spot * 0.98, 2)
    try:
        opts = options_layer.analyze("SPY", score_result, spot, target, stop,
                                     mode="swing", iv_rank=ivr,
                                     dte_target=config.DIPBUY_FORWARD_DTE)
    except Exception as e:
        logger.warning(f"dipbuy_forward: analyze failed: {e}")
        return None
    if not opts or not opts.get("legs"):
        logger.info("dipbuy_forward: no priceable bull structure today")
        return None

    from learning.paper_broker import AUTO_SOURCE  # late import avoids circular dep
    tid = recorder.log_entry(
        ticker="SPY",
        entry_price=float(opts.get("entry_price") or opts.get("net_premium") or 1.0),
        size=1,
        trade_type=opts.get("strategy", "bull_debit"),
        strategy=opts.get("strategy", "bull_debit"),
        direction="bullish",
        mode="swing",
        legs=opts.get("legs", []),
        max_profit=opts.get("max_profit"),
        max_loss=opts.get("max_loss"),
        notes=f"[CANDIDATE {today.isoformat()}] oversold dip-buy forward-test",
        dte_bucket="dipbuy",
        book=config.DIPBUY_FORWARD_BOOK,
        source=AUTO_SOURCE,
    )
    logger.info(f"dipbuy_forward: recorded candidate {tid} (entry_spot={spot})")
    return {"recorded": True, "trade_id": tid}
