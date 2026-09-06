"""learning/ladder_forward.py -- 14DTE and 21DTE SPY condor PAPER forward-test.

docs/DTE_LADDER_EXIT_STUDY.md swept every rung with tail metrics (median, p05,
worst, win/loss ratio, mean/sigma, max adverse excursion), OOS era-split, 10%
haircut and commissions. Two rungs we do not trade tested better than
everything we do:

    rung   best exit   avg      mean/sigma   verdict
    14DTE  close at 1  +$49.47  0.413        PASS both eras
    21DTE  close at 2  +$42.06  0.339        PASS both eras
    ---- for comparison ----
    7DTE   close at 1  +$38.52  0.395        (live)
    45DTE  close at 21  -$1.41  -0.018       (live, disciplined book)

They also carry less path pain than the long rungs (MAE -$55/-$66 against
-$91 at 45DTE).

EXITS ARE DERIVED PER RUNG, NOT SCALED. `round(dte * 21/45)` would give 7 and
10 here; those are the wrong answers. Proportional scaling of a time rule is
invalid because theta is convex in DTE — it is the defect that cost the 7DTE
book ~$28/trade (docs/SEVEN_DTE_STRUCTURE_STUDY.md). Each rung's close came
from its own sweep, ranked on mean/sigma rather than raw mean, with a minimum
1-day buffer so we never hold into expiry day and risk assignment.

PROMOTION BAR, fixed here at creation so the goalposts cannot move:
    >= 15 closed, win rate >= 70%, avg > $20/trade NET of commissions,
    and no single loss beyond max_loss.

OPEN QUESTION, recorded so it is not quietly forgotten: the study found the
calm-regime filter helps these rungs in 2023+ but HURT them in 2018-22, while
the UNFILTERED condor was markedly more era-stable. These candidates run
filtered, for consistency with the rest of the book and because both rungs
still pass OOS with the filter on. Whether unfiltered is better is a live
question, not a settled one.

Self-contained per Standing Rule #10.
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

# dte -> its OWN swept exit. Never derive one of these from another.
RUNGS = {
    14: {"close_dte": 1, "bucket": "14DTE",
         "evidence": "+$49.47/trade, mean/sigma 0.413, PASS both eras"},
    21: {"close_dte": 2, "bucket": "21DTE",
         "evidence": "+$42.06/trade, mean/sigma 0.339, PASS both eras"},
}

PROMOTION_BAR = ("n>=15 closed, win>=70%, avg>$20 net of fees, "
                 "no loss>max_loss")
MIN_N, MIN_WIN, MIN_AVG = 15, 0.70, 20.0


def _today_et() -> _date:
    """The journal stamps entry_date in US/Eastern — date comparisons must use
    the SAME zone or the 11pm-midnight window double-opens (found 07-10)."""
    return _datetime.now(pytz.timezone("US/Eastern")).date()


def _meets_bar(n: int, wins: int, avg: float) -> bool:
    return bool(n >= MIN_N and (wins / n if n else 0) >= MIN_WIN and avg > MIN_AVG)


def maybe_open_ladder(recorder, *, spy_spot, vix, today=None) -> list[dict]:
    """Open a 1-lot paper condor on each rung. Caller decides WHEN (a
    condor-regime day). Idempotent per (day, rung)."""
    if not getattr(config, "LADDER_FORWARD_ENABLED", True):
        return []
    if not config.within_entry_window():
        return []
    today = today or _today_et()
    rungs = getattr(config, "LADDER_FORWARD_DTES", tuple(RUNGS))

    from signals.condor_calc import build_condor
    from learning.paper_broker import AUTO_SOURCE

    opened = []
    open_trades = recorder.get_open_trades()
    for dte in rungs:
        cfg = RUNGS.get(dte)
        if not cfg:
            continue
        bucket = cfg["bucket"]
        if any(t.get("dte_bucket") == bucket
               and str(t.get("entry_date", "")).startswith(today.isoformat())
               for t in open_trades):
            logger.info(f"ladder_forward: {bucket} already open today — skip")
            continue
        c = build_condor(spy_spot, vix, dte=dte)
        if not c or c["credit"] <= 0:
            logger.info(f"ladder_forward: {bucket} no credit at current vol — skip")
            continue
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
            notes=f"[CANDIDATE {today.isoformat()}] {bucket} condor forward-test "
                  f"(close at {cfg['close_dte']} DTE, derived not scaled; "
                  f"{cfg['evidence']}; promotion bar: {PROMOTION_BAR})",
            dte_bucket=bucket,
            book=config.DIPBUY_FORWARD_BOOK,
            source=AUTO_SOURCE,
        )
        logger.info(f"ladder_forward: recorded {bucket} candidate {tid} "
                    f"(SPY {spy_spot:.2f}, credit {c['credit']:.2f})")
        opened.append({"recorded": True, "trade_id": tid, "dte_bucket": bucket})
    return opened


def resolve_ladder(recorder, *, spy_spot, vix, today=None) -> list[dict]:
    """Mark + close open ladder candidates at 70% of max profit or the rung's
    own time stop."""
    today = today or _today_et()
    by_bucket = {cfg["bucket"]: cfg for cfg in RUNGS.values()}
    closed = []
    for t in recorder.get_all_trades():
        cfg = by_bucket.get(t.get("dte_bucket"))
        if cfg is None or t.get("outcome") not in (None, "open"):
            continue
        if t.get("book") != config.DIPBUY_FORWARD_BOOK:
            continue          # a promoted position is the exit manager's job
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
        hit_time = dte_left <= cfg["close_dte"]
        if hit_target or hit_time:
            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(t["trade_id"], round(cost, 2),
                              notes=f"[CANDIDATE close {today.isoformat()}] {reason} "
                                    f"(SPY {spy_spot:.2f})",
                              exit_reason=reason)
            closed.append({**t, "exit_reason": reason})
    return closed


def paper_record(recorder) -> dict:
    """Per-rung progress vs the promotion bar. Averages are NET of commissions,
    because the bar is judged net (audit A4)."""
    from learning.forward_scorecard import net_pnl
    out = {}
    trades = recorder.get_all_trades()
    for dte, cfg in RUNGS.items():
        rows = [t for t in trades
                if t.get("dte_bucket") == cfg["bucket"]
                and t.get("pnl_dollars") is not None]
        nets = [p for p in (net_pnl(t) for t in rows) if p is not None]
        n = len(nets)
        wins = sum(1 for p in nets if p > 0)
        avg = sum(nets) / n if n else 0.0
        out[dte] = {
            "n": n, "wins": wins,
            "win_pct": (wins / n * 100) if n else 0.0,
            "avg": round(avg, 2),
            "close_dte": cfg["close_dte"],
            "bar": PROMOTION_BAR,
            "meets_bar": _meets_bar(n, wins, avg),
        }
    return out
