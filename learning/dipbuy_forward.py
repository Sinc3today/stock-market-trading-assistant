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
    yesterday>=30) — the validated dip-buy trigger."""
    if spy_df is None or len(spy_df) < 30:
        return False
    trig = oversold_triggers(rsi_series(spy_df["close"].astype(float), 14), 30.0)
    return bool(trig.iloc[-1])


def is_fresh_breakdown(spy_df, window: int = None) -> bool:
    """True iff the latest daily bar FRESHLY closes below the prior `window`-day
    low (Donchian breakdown) — the validated complementary 'buy weakness' trigger
    (50d-low breakdowns bounce +1.65/2.51%, see docs/DIPBUY_BREAKDOWN_STUDY.md)."""
    window = window or config.DIPBUY_BREAKDOWN_WINDOW
    if spy_df is None or len(spy_df) < window + 1:
        return False
    close = spy_df["close"].astype(float)
    prior_low = close.rolling(window).min().shift(1)
    beyond = close < prior_low
    fresh = beyond & ~beyond.shift(1).fillna(False)
    return bool(fresh.iloc[-1])


def dip_signal(spy_df) -> str | None:
    """Which buy-weakness trigger fired on the latest bar (oversold takes
    priority), or None. Both are validated dip-buy triggers."""
    if is_fresh_oversold(spy_df):
        return "oversold"
    if is_fresh_breakdown(spy_df):
        return "breakdown"
    return None


def maybe_open_dipbuy(spy_df, *, spot, ivr, options_layer, recorder, today=None):
    """On a fresh oversold cross, build a bull-call debit (~21 DTE) via the same
    OptionsLayer the live bull plays use and record a 1-ct paper trade in the
    'candidate' book. Idempotent per day. Returns {'recorded': True,
    'trade_id': tid} or None."""
    if not config.DIPBUY_FORWARD_ENABLED:
        return None
    if not config.within_entry_window():
        logger.info("dipbuy_forward: outside entry window (09:45-15:00 ET) — no open")
        return None
    sig = dip_signal(spy_df)
    if sig is None:
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
        notes=f"[CANDIDATE {today.isoformat()}] {sig} dip-buy forward-test",
        dte_bucket="dipbuy",
        book=config.DIPBUY_FORWARD_BOOK,
        source=AUTO_SOURCE,
    )
    logger.info(f"dipbuy_forward: recorded candidate {tid} (entry_spot={spot})")
    return {"recorded": True, "trade_id": tid}


# ── Resolver (50%-of-max-profit / 10-trading-day hold) ──────────────────────

from learning.exit_manager import bs_price


def _mark_spread(legs, spot, vix, dte_days) -> float:
    """Net per-share value of the spread (long legs − short legs), BS off spot.
    For a bull-call debit this is positive and rises with spot."""
    sigma = vix / 100.0
    t = max(dte_days, 0) / 365.0
    val = 0.0
    for leg in legs:
        otype = (leg.get("type") or leg.get("option_type") or "call").lower()
        p = bs_price(otype, spot, float(leg["strike"]), t, sigma)
        val += p if leg.get("action") == "BUY" else -p
    return val


def resolve_candidates(recorder, *, spy_close, vix, today=None):
    """Mark + close open 'candidate' trades: 50%-of-max-profit OR
    DIPBUY_FORWARD_MAX_HOLD_TD trading days held. The caller wraps this per
    Standing Rule #10. Returns the list of closed trade dicts."""
    today = today or _date.today()
    trades = recorder.get_all_trades()
    closed, dirty = [], False
    for t in trades:
        if t.get("book") != config.DIPBUY_FORWARD_BOOK:
            continue
        if t.get("outcome") not in (None, "open"):
            continue
        td = int(t.get("td_held", 0)) + 1
        t["td_held"] = td
        dirty = True
        legs = t.get("legs") or []
        dte_left = max(config.DIPBUY_FORWARD_DTE - td, 1)
        mark = _mark_spread(legs, spy_close, vix, dte_left)
        pnl  = (mark - float(t.get("entry_price", 0.0))) * 100 * int(t.get("size", 1))
        mp   = t.get("max_profit") or 0.0
        hit_target = mp > 0 and pnl >= config.DIPBUY_FORWARD_TARGET_PCT * mp
        hit_hold   = td >= config.DIPBUY_FORWARD_MAX_HOLD_TD
        if hit_target or hit_hold:
            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(t["trade_id"], round(mark, 2),
                              notes=f"[CANDIDATE close {today.isoformat()}] {reason}",
                              exit_reason=reason)
            closed.append(t)
    if dirty:
        recorder._save(trades)
    return closed
