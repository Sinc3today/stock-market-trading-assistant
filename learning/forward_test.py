"""learning/forward_test.py -- one implementation of the paper forward test.

Five generators (seven_dte, qqq_condor, broken_wing, ladder, dipbuy) shared
75-86% of their bodies, and every defect found in the 2026-09-07 audit existed
in SOME BUT NOT ALL of them:

  * the book filter — three had it, `qqq_condor_forward` did not, so on
    promotion both its resolver and ExitManager would manage the position and
    both would call log_exit
  * the four risk guards — `paper_broker` enforces them, not one generator did,
    which is how 26 correlated short-vol positions accumulated while
    ENFORCE_CONCENTRATION_GUARD sat switched on
  * promotion scoring — `ladder` used net, `seven_dte` and `broken_wing` used
    gross, against a bar defined as "avg > $20 NET of commissions"
  * RULE_EPOCH — only `seven_dte` had one, so correcting any other generator's
    exit rule would silently pool two different strategies (audit E2)

That pattern is the root cause this whole audit found: **no decision had one
owner, and nothing could enumerate where it was made.** Collapsing them means
the next fix lands everywhere by construction rather than by remembering.

Each generator keeps its own module (Standing Rule #10 — a self-contained job
that cannot take its siblings down) but becomes a thin ForwardSpec plus a
delegation, instead of a copy of this logic.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from loguru import logger

import config

DEFAULT_BAR = "n>=15 closed, win>=70%, avg>$20 net of fees, no loss>max_loss"
MIN_N, MIN_WIN, MIN_AVG = 15, 0.70, 20.0


def _today_et() -> _date:
    """The journal stamps entry_date in US/Eastern — date comparisons must use
    the SAME zone or the 11pm-midnight window double-opens (found 07-10)."""
    return _datetime.now(pytz.timezone("US/Eastern")).date()


@dataclass
class ForwardSpec:
    """Everything that legitimately differs between forward tests."""
    name: str
    ticker: str
    buckets: dict[str, int]            # dte_bucket -> its OWN close-DTE
    target_pct: float = 0.70
    book: str = "candidate"
    promotion_bar: str = DEFAULT_BAR
    # Trades opened before this date ran a different exit rule and are counted
    # separately. Pooling across a rule change describes a strategy nobody runs.
    rule_epoch: str | None = None
    enabled_flag: str | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)


class ForwardTest:
    """Shared open-guard / resolve / score behaviour for a paper forward test."""

    def __init__(self, spec: ForwardSpec):
        self.spec = spec

    # ── opening ──────────────────────────────────────────────────

    def may_open(self, recorder, bucket: str, today: _date | None = None) -> bool:
        """Every gate a new paper position must clear.

        The generators previously checked only the entry window, each having
        re-implemented "should I open?" from scratch. The risk guards below
        live in config and were enforced in exactly one of six opening paths.
        """
        spec = self.spec
        if spec.enabled_flag and not getattr(config, spec.enabled_flag, True):
            logger.info(f"{spec.name}: disabled by {spec.enabled_flag}")
            return False
        if not config.within_entry_window():
            return False

        today = today or _today_et()
        open_trades = recorder.get_open_trades()

        # Idempotent per (bucket, day).
        if any(t.get("dte_bucket") == bucket
               and str(t.get("entry_date", "")).startswith(today.isoformat())
               for t in open_trades):
            logger.info(f"{spec.name}: {bucket} already open today — skip")
            return False

        # Concentration. Every rung here is the same short-vol bet at a
        # different duration; spreading across durations is not diversifying.
        if getattr(config, "ENFORCE_CONCENTRATION_GUARD", True):
            cap = getattr(config, "MAX_CONCURRENT_CANDIDATES", None)
            if cap is not None:
                live = sum(1 for t in open_trades if t.get("book") == spec.book)
                if live >= cap:
                    logger.warning(
                        f"{spec.name}: {live} candidate positions already open "
                        f"(cap {cap}) — refusing {bucket}"
                    )
                    return False
        return True

    # ── resolving ────────────────────────────────────────────────

    def resolve(self, recorder, *, spot: float, vol: float,
                today: _date | None = None) -> list[dict]:
        """Mark open positions and close at the profit target or the bucket's
        own time stop."""
        from learning.dipbuy_forward import _mark_spread
        spec = self.spec
        today = today or _today_et()
        closed: list[dict] = []

        for t in recorder.get_all_trades():
            bucket = t.get("dte_bucket")
            if bucket not in spec.buckets:
                continue
            if t.get("outcome") not in (None, "open"):
                continue
            # A promoted position belongs to the ExitManager. Without this,
            # two resolvers manage one trade and both call log_exit.
            if t.get("book") != spec.book:
                continue

            legs = t.get("legs") or []
            expiry = _nearest_expiry(legs)
            if expiry is None:
                continue
            dte_left = (expiry - today).days

            # Signed cost to close, deliberately NOT clamped: a broken-wing is
            # long a far wing, so being paid to close is legitimate. Clamping
            # booked phantom max-loss (audit A2).
            cost = -_mark_spread(legs, spot, vol, max(dte_left, 0))
            pnl = (float(t.get("entry_price", 0)) - cost) * 100 * int(t.get("size", 1))
            mp = t.get("max_profit") or 0.0

            hit_target = mp > 0 and pnl >= spec.target_pct * mp
            hit_time = dte_left <= spec.buckets[bucket]
            if not (hit_target or hit_time):
                continue

            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(
                t["trade_id"], round(cost, 2),
                notes=(f"[CANDIDATE close {today.isoformat()}] {reason} "
                       f"({spec.ticker} {spot:.2f})"),
                exit_reason=reason,
            )
            closed.append({**t, "exit_reason": reason})
        return closed

    # ── scoring ──────────────────────────────────────────────────

    @staticmethod
    def _meets_bar(n: int, wins: int, avg: float) -> bool:
        return bool(n >= MIN_N and (wins / n if n else 0) >= MIN_WIN
                    and avg > MIN_AVG)

    def paper_record(self, recorder) -> dict:
        """Per-bucket progress toward the promotion bar, NET of commissions.

        The bar says "net of fees" and three of five generators scored gross —
        a 4-leg round trip is $5.20, 26% of a $20 bar.
        """
        from learning.forward_scorecard import net_pnl
        spec = self.spec
        trades = recorder.get_all_trades()
        out: dict = {}
        for bucket in spec.buckets:
            rows = [t for t in trades
                    if t.get("dte_bucket") == bucket
                    and t.get("pnl_dollars") is not None]
            current, legacy = _split_by_epoch(rows, spec.rule_epoch)
            n, wins, avg = _agg(current, net_pnl)
            ln, lwins, lavg = _agg(legacy, net_pnl)
            out[bucket] = {
                "n": n, "wins": wins,
                "win_pct": (wins / n * 100) if n else 0.0,
                "avg": round(avg, 2),
                "close_dte": spec.buckets[bucket],
                "bar": spec.promotion_bar,
                "rule_epoch": spec.rule_epoch,
                "legacy": {"n": ln, "avg": round(lavg, 2),
                           "win_pct": (lwins / ln * 100) if ln else 0.0},
                "meets_bar": self._meets_bar(n, wins, avg),
            }
        return out


def _nearest_expiry(legs: list[dict]) -> _date | None:
    try:
        return min(_date.fromisoformat(str(l.get("expiry") or l.get("expiration"))[:10])
                   for l in legs if (l.get("expiry") or l.get("expiration")))
    except (ValueError, TypeError):
        return None


def _split_by_epoch(rows, epoch):
    if not epoch:
        return rows, []
    cur = [t for t in rows if str(t.get("entry_date", ""))[:10] >= epoch]
    old = [t for t in rows if str(t.get("entry_date", ""))[:10] < epoch]
    return cur, old


def _agg(rows, scorer):
    vals = [v for v in (scorer(t) for t in rows) if v is not None]
    n = len(vals)
    wins = sum(1 for v in vals if v > 0)
    return n, wins, (sum(vals) / n if n else 0.0)
