"""
signals/intraday_entry_router.py -- Phase 3 entry-side decision module.

A pure function that takes a SPYSetup + current ET datetime + a PaperBroker
and returns 0..2 setup_dicts ready for PaperBroker.execute_signal.

Applies, in order:
  1. Entry-tier filter (conviction >= config.ENTRY_TIER_MINIMUM)
  2. H2 DTE assignment (time-of-day + Friday-PM safeguard + ultra-conv double)
  3. D dedup (one open per combo + per-day cap per combo)

Returns an empty list when all candidate buckets are blocked, or when the
setup fails the entry-tier filter. The MAX_CONCURRENT_DISCIPLINED cap is
enforced downstream inside execute_signal (Phase 2b), not here.

Phase 3 ships with placeholder pricing in the setup_dict (entry_price=1.0,
max_profit=200.0, max_loss=100.0, legs=[]). Phase 4's structure builder
will replace these with real per-sub-strategy strikes and pricing.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# Ordered tiers so we can compare ">=" against ENTRY_TIER_MINIMUM.
_TIER_RANK = {"watch": 0, "standard": 1, "high": 2}


def _passes_entry_tier(setup) -> bool:
    """Setup's conviction must rank >= config.ENTRY_TIER_MINIMUM."""
    return _TIER_RANK.get(setup.conviction, -1) >= _TIER_RANK.get(
        config.ENTRY_TIER_MINIMUM, _TIER_RANK["high"]
    )


def _assign_dte_buckets(setup, now: datetime) -> list[str]:
    """H2 rule: time-of-day discriminator with Friday-PM safeguard and
    ultra-conviction doubling.

    Returns the list of dte_buckets the setup should attempt to open in
    (before dedup filtering)."""
    cutoff_h, cutoff_m = (int(x) for x in config.INTRADAY_DTE_MORNING_CUTOFF.split(":"))
    morning_cutoff = time(cutoff_h, cutoff_m)

    is_friday    = now.weekday() == 4
    is_afternoon = now.time() >= morning_cutoff
    is_friday_pm = is_friday and is_afternoon

    # Ultra-conviction → both buckets, EXCEPT Friday PM (no weekend exposure).
    if setup.score >= config.ULTRA_CONVICTION_DOUBLE_DTE_SCORE and not is_friday_pm:
        return ["0DTE", "1-3DTE"]

    # Friday PM safeguard: always 0DTE, never 1-3DTE.
    if is_friday_pm:
        return ["0DTE"]

    # Default H2: morning → 0DTE, afternoon → 1-3DTE.
    return ["1-3DTE"] if is_afternoon else ["0DTE"]


def _dedup_filter(strategy: str, dte_buckets: list[str], broker) -> list[str]:
    """D rule: drop a bucket if a position is already open in (strategy, bucket)
    OR today's entry count for the combo has reached
    config.INTRADAY_PER_COMBO_DAILY_CAP."""
    allowed = []
    for bucket in dte_buckets:
        # Check 1: any position currently open in this combo?
        open_in_combo = [
            t for t in broker.trades.get_trades_by(strategy=strategy, dte_bucket=bucket)
            if t.get("outcome") == "open"
        ]
        if open_in_combo:
            continue

        # Check 2: today's entry count under the per-day cap?
        n_today = broker._entry_count_today_by_combo(strategy, bucket)
        if n_today >= config.INTRADAY_PER_COMBO_DAILY_CAP:
            continue

        allowed.append(bucket)
    return allowed


def _build_setup_dict(setup, dte_bucket: str, now: datetime) -> dict:
    """Construct a setup_dict in the shape PaperBroker.execute_signal expects.

    Phase 3 uses placeholder pricing values. Phase 4's structure builder will
    replace these with real per-sub-strategy strikes."""
    return {
        "date":        now.date().isoformat(),
        "strategy":    setup.strategy,
        "dte_bucket":  dte_bucket,
        "book":        "disciplined",
        "direction":   (setup.direction or "neutral").lower(),
        # Phase 3 placeholders — see docstring + spec §Honesty Caveats.
        "entry_price": 1.0,
        "max_profit":  200.0,
        "max_loss":    100.0,
        "legs":        [],
    }


def route(setup, now: datetime, broker) -> list[dict]:
    """Convert a SPYSetup into 0..2 setup_dicts.

    setup:  a SPYSetup from signals.spy_options_engine
    now:    current datetime in US/Eastern (tz-aware)
    broker: a PaperBroker instance (needed for the dedup state queries)
    """
    if not _passes_entry_tier(setup):
        return []

    buckets    = _assign_dte_buckets(setup, now)
    allowed    = _dedup_filter(setup.strategy, buckets, broker)
    return [_build_setup_dict(setup, b, now) for b in allowed]
