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
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# Ordered tiers so we can compare ">=" against ENTRY_TIER_MINIMUM.
_TIER_RANK = {"watch": 0, "standard": 1, "high": 2}


def _synthesize_legs(strategy: str, dte_bucket: str, now: datetime) -> list[dict]:
    """Phase 4a hotfix: build a placeholder leg so ExitManager can dispatch.

    Phase 4b's structure builder will replace this with real strikes/wings.
    Until then, synthesize a single leg with the correct expiration date
    derived from dte_bucket so ExitManager._nearest_expiration() returns a
    real date instead of None — preventing Phase 3 positions from becoming
    orphans that accumulate and consume MAX_CONCURRENT_DISCIPLINED slots.

    Both 'expiry' (TradeRecorder convention) and 'expiration' (exit_manager
    convention) are populated for compatibility. The 'synthetic' flag marks
    these as placeholders so Phase 4b's structure builder can replace them.
    """
    today = now.date()
    if dte_bucket == "0DTE":
        exp = today
    elif dte_bucket == "1-3DTE":
        exp = today + timedelta(days=2)   # midpoint of the 1-3 day range
    elif dte_bucket == "45DTE":
        exp = today + timedelta(days=45)
    else:
        exp = today   # safe fallback for any future bucket

    # option_type heuristic: iron_condor and debit spreads lean CALL for the
    # synthetic placeholder; Phase 4b will set the real type per sub-strategy.
    option_type = "PUT" if strategy == "put_debit_spread" else "CALL"

    return [{
        "action":      "BUY",
        "option_type": option_type,
        "strike":      0.0,          # placeholder — Phase 4b builds real strikes
        "expiry":      exp.isoformat(),
        "expiration":  exp.isoformat(),  # both keys for exit_manager compat
        "synthetic":   True,             # flag so structure builder can replace
    }]


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


def _dte_reject_detail(setup, now: datetime, bucket: str) -> str:
    """Human-readable reason `bucket` was not in _assign_dte_buckets's output."""
    cutoff_h, cutoff_m = (int(x) for x in config.INTRADAY_DTE_MORNING_CUTOFF.split(":"))
    is_friday    = now.weekday() == 4
    is_afternoon = now.time() >= time(cutoff_h, cutoff_m)
    if is_friday and is_afternoon and bucket == "1-3DTE":
        return "Friday-PM safeguard: 1-3DTE dropped (no weekend exposure)"
    if bucket == "0DTE":
        return "afternoon → 1-3DTE assigned, 0DTE not selected"
    return "morning → 0DTE assigned, 1-3DTE not selected"


def _dedup_partition(strategy: str, dte_buckets: list[str], broker
                     ) -> tuple[list[str], list[tuple[str, str]]]:
    """D rule, with reasons. Returns (allowed, [(bucket, reason), ...]).

    A bucket is dropped if a position is already open in (strategy, bucket)
    OR today's entry count for the combo has reached
    config.INTRADAY_PER_COMBO_DAILY_CAP. This is the single dedup
    implementation; _dedup_filter wraps it so route() is unchanged.
    """
    allowed: list[str] = []
    rejected: list[tuple[str, str]] = []
    for bucket in dte_buckets:
        open_in_combo = [
            t for t in broker.trades.get_trades_by(strategy=strategy, dte_bucket=bucket)
            if t.get("outcome") == "open"
        ]
        if open_in_combo:
            rejected.append((bucket, f"open position already in ({strategy}, {bucket})"))
            continue
        n_today = broker._entry_count_today_by_combo(strategy, bucket)
        if n_today >= config.INTRADAY_PER_COMBO_DAILY_CAP:
            rejected.append((bucket,
                f"per-combo daily cap reached ({n_today} >= {config.INTRADAY_PER_COMBO_DAILY_CAP})"))
            continue
        allowed.append(bucket)
    return allowed, rejected


def _dedup_filter(strategy: str, dte_buckets: list[str], broker) -> list[str]:
    """D rule: drop a bucket if a position is already open in (strategy, bucket)
    OR today's entry count for the combo has reached
    config.INTRADAY_PER_COMBO_DAILY_CAP. Thin wrapper over _dedup_partition so
    route()'s observable behavior is identical."""
    allowed, _ = _dedup_partition(strategy, dte_buckets, broker)
    return allowed


def _build_setup_dict(setup, dte_bucket: str, now: datetime) -> dict:
    """Construct a setup_dict in the shape PaperBroker.execute_signal expects.

    Phase 3 uses placeholder pricing values. Phase 4's structure builder will
    replace these with real per-sub-strategy strikes.

    C4 hotfix: legs now contains a synthetic placeholder leg (not []) so
    ExitManager._nearest_expiration can return a real expiration date and
    dispatch the position. Without this, Phase 3 positions become orphans.
    """
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
        # C4 hotfix: synthetic placeholder legs so ExitManager can dispatch.
        # Phase 4b's structure builder will replace these with real strikes.
        "legs":        _synthesize_legs(setup.strategy, dte_bucket, now),
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
