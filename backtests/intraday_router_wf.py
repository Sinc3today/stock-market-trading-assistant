"""
backtests/intraday_router_wf.py -- Walk-forward backtest of the Phase 3
intraday entry router.

Wraps backtests/intraday_backtest.simulate_0dte_day with
signals/intraday_entry_router.route. Runs treatment (router-gated) vs
baseline (tier-gate disabled) on identical days, identical structures.
Emits raw per-window stats; verdict thresholds are TBD via a follow-up
calibration exercise.

Spec: docs/superpowers/specs/2026-05-28-intraday-router-wf-design.md
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─────────────────────────────────────────────────────────────
# Mock broker — satisfies signals.intraday_entry_router.route's
# dedup-state queries with per-day in-memory state.
# ─────────────────────────────────────────────────────────────

class _MockBroker:
    """Minimal broker stub for route(). Fresh-per-day in the runner so
    cross-day state can't leak. Implements only the two methods route()
    calls: trades.get_trades_by and _entry_count_today_by_combo."""

    def __init__(self):
        self.trades = self   # adapter so route() can call .trades.get_trades_by()
        self._opens: list[dict] = []

    def get_trades_by(self, *, strategy: str, dte_bucket: str) -> list[dict]:
        return [t for t in self._opens
                if t["strategy"] == strategy and t["dte_bucket"] == dte_bucket]

    def _entry_count_today_by_combo(self, strategy: str, dte_bucket: str) -> int:
        return len(self.get_trades_by(strategy=strategy, dte_bucket=dte_bucket))

    def record_open(self, *, strategy: str, dte_bucket: str) -> None:
        self._opens.append({
            "strategy":   strategy,
            "dte_bucket": dte_bucket,
            "outcome":    "open",
        })


from contextlib import contextmanager

import config


@contextmanager
def _bypass_tier_gate():
    """Temporarily set config.ENTRY_TIER_MINIMUM = 'watch' (the lowest rank
    in signals.intraday_entry_router._TIER_RANK) so route()'s tier gate
    admits everything. Used to compute the BASELINE side of the WF
    comparison — DTE assignment and dedup remain identical to treatment,
    so the only delta is the tier filter.

    Restoration is guaranteed: the original value is captured at __enter__,
    not read from config at __exit__, so caller mutations inside the
    block don't break restoration.
    """
    original = config.ENTRY_TIER_MINIMUM
    config.ENTRY_TIER_MINIMUM = "watch"
    try:
        yield
    finally:
        config.ENTRY_TIER_MINIMUM = original


from datetime import date, timedelta
from typing import Iterator


def _add_months(d: date, n: int) -> date:
    """Add n calendar months to date d, clipping the day to the new month's
    last day if necessary. Used for window boundary math."""
    month = d.month + n
    year  = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    # Clip day to month's last day to avoid 31->Feb errors.
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def generate_windows(start: date, end: date,
                     train_months: int = 6, test_months: int = 3,
                     step_months: int = 1
                     ) -> Iterator[tuple[tuple[date, date], tuple[date, date]]]:
    """Yield (train_range, test_range) tuples where each range is
    (start_date_inclusive, end_date_inclusive).

    Sliding walk-forward: train covers `train_months` calendar months
    immediately preceding test; test covers the next `test_months`. Each
    iteration advances the test_start by `step_months`. Stops when the test
    range would overshoot `end`.

    Train window has no learning role in this spec — it's a contextual
    placeholder for a future learning step.
    """
    # Anchor test_start to first-of-month so windows align to calendar
    # months regardless of the input `start` day-of-month.
    anchored = _add_months(start, train_months)
    test_start = date(anchored.year, anchored.month, 1)
    while True:
        train_start = _add_months(test_start, -train_months)
        train_end   = test_start - timedelta(days=1)
        test_end    = _add_months(test_start, test_months) - timedelta(days=1)
        if test_end > end:
            return
        yield ((train_start, train_end), (test_start, test_end))
        test_start = _add_months(test_start, step_months)


STRATEGY_NOT_SUPPORTED = object()   # sentinel — router emitted a strategy
                                     # backtests/intraday_backtest.py can't price


def _strategy_to_structure(strategy: str, direction: str):
    """Map signals.intraday_entry_router setup.strategy → backtests.
    intraday_backtest structure name. Returns STRATEGY_NOT_SUPPORTED if
    the strategy can't be priced (out of scope for v1)."""
    if strategy == "iron_condor":
        return "iron_condor"
    if strategy == "call_debit_spread":
        return "bull_debit"
    if strategy == "put_debit_spread":
        return "bear_debit"
    return STRATEGY_NOT_SUPPORTED


def simulate_short_dte_day(day, structure: str, dte_bucket: str,
                            spy_intraday, options_history):
    """Wrap backtests.intraday_backtest.simulate_0dte_day to support 0DTE
    AND 1-3DTE in the same call. The 0DTE path delegates directly; the
    1-3DTE path picks a future-expiration contract and exits at session
    close instead of the 0DTE EOD pin/assignment flatten.

    Treatment and baseline both call this with require_confirmation=False
    so the router IS the entry filter (OR+VWAP would double-gate otherwise).

    Returns simulate_0dte_day's result dict, or None when the day can't
    be priced.
    """
    from datetime import timedelta
    from backtests.intraday_backtest import simulate_0dte_day

    if dte_bucket == "0DTE":
        return simulate_0dte_day(
            day, structure, spy_intraday, options_history,
            require_confirmation=False,   # router replaces OR+VWAP
        )

    if dte_bucket == "1-3DTE":
        return _simulate_short_dte_with_expiration(
            day, day + timedelta(days=2),
            structure, spy_intraday, options_history,
        )

    return None   # unknown bucket — caller's bug


def _simulate_short_dte_with_expiration(day, expiry,
                                         structure: str,
                                         spy_intraday, options_history):
    """1-3DTE same-session simulator. Same opening-range entry as the 0DTE
    simulator, but the option contract has `expiry > day`, so:
      - There's no pin/assignment risk on `day`, hence no 15:45 flatten —
        we exit at the regular session close (16:00) or on target/stop.
      - This is a SAME-DAY-MARK approximation: we record entry-to-close
        PnL on `day` for a contract that has additional days to live.
        Full multi-day PnL is out of scope for v1 — documented in spec.
    """
    from datetime import datetime, timedelta, time
    from data.options_history import option_ticker
    from backtests.intraday_backtest import (
        _to_et, _spread_value, build_0dte_legs, is_credit_structure,
        MARKET_OPEN_ET, OR_MINUTES, COMMISSION_PER_LEG, SLIPPAGE,
        PROFIT_TARGET_PCT, STOP_MULT,
    )
    import pandas as pd

    if spy_intraday is None or spy_intraday.empty:
        return None
    spy = _to_et(spy_intraday)
    SESSION_CLOSE_ET = time(16, 0)
    rth = spy[(spy.index.time >= MARKET_OPEN_ET) & (spy.index.time <= SESSION_CLOSE_ET)]
    if rth.empty:
        return None

    or_end = (datetime.combine(day, MARKET_OPEN_ET) + timedelta(minutes=OR_MINUTES)).time()
    session = rth[rth.index.time >= or_end]
    if session.empty:
        return None

    entry_ts   = session.index[0]
    entry_spot = float(session.iloc[0]["close"])

    legs = build_0dte_legs(entry_spot, structure)
    if not legs:
        return None

    leg_closes = []
    for leg in legs:
        contract = option_ticker("SPY", expiry, leg["cp"], leg["strike"])
        df = options_history.get_aggs(contract, 5, "minute", day, day)
        if df.empty:
            return None
        s = _to_et(df)["close"]
        leg_closes.append((leg, s))

    def marks_at(ts):
        out = []
        for leg, s in leg_closes:
            at = s[s.index <= ts]
            if at.empty:
                return None
            out.append((leg, float(at.iloc[-1])))
        return out

    credit = is_credit_structure(structure)
    entry_marks = marks_at(entry_ts)
    if entry_marks is None:
        return None
    entry_px = _spread_value(entry_marks, structure)
    entry_px = (entry_px - SLIPPAGE) if credit else (entry_px + SLIPPAGE)
    if entry_px <= 0:
        return None

    width      = abs(legs[0]["strike"] - legs[1]["strike"]) if len(legs) >= 2 else 0
    max_profit = entry_px * 100 if credit else (width - entry_px) * 100
    commission = COMMISSION_PER_LEG * len(legs) * 2

    exit_reason = "session_close"
    pnl = -commission
    for ts in session.index:
        m = marks_at(ts)
        if m is None:
            continue
        val = _spread_value(m, structure)
        if credit:
            cost = val + SLIPPAGE
            pnl  = (entry_px - cost) * 100 - commission
        else:
            proceeds = max(0.0, val - SLIPPAGE)
            pnl      = (proceeds - entry_px) * 100 - commission
        if max_profit > 0 and pnl >= PROFIT_TARGET_PCT * max_profit:
            exit_reason = "target"; break
        if STOP_MULT is not None and pnl <= -STOP_MULT * max_profit:
            exit_reason = "stop"; break

    return {
        "date": day.isoformat(), "structure": structure,
        "entry_spot": round(entry_spot, 2), "entry_px": round(entry_px, 2),
        "pnl_dollars": round(pnl, 2),
        "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "exit_reason": exit_reason,
    }
