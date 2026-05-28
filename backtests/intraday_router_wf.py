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
