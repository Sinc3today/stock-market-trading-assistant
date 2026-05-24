"""Phase 4a item 0b — backfill seed script.

Runs `backtests/spy_daily_backtest.py` over the last ~90 calendar days
(~60 trading days), transforms each backtested trading day into a
TradeRecorder-schema record, and writes them to
`logs/simulated_trades.json` with `simulated: True`.

Idempotency: refuses to overwrite an existing simulated_trades.json
unless --force is passed (creates a .bak backup first).

Usage:
    python -m scripts.seed_simulated_trades
    python -m scripts.seed_simulated_trades --days 90 --force
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import date, timedelta

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# Map daily-backtest "play" → (strategy, direction) for TradeRecorder schema
PLAY_TO_STRATEGY = {
    "iron_condor": ("iron_condor", "NEUTRAL"),
    "bull_debit":  ("debit_spread",  "BULLISH"),
    "bear_debit":  ("debit_spread",  "BEARISH"),
    "bull_credit": ("credit_spread", "BULLISH"),
    "bear_credit": ("credit_spread", "BEARISH"),
}


def _run_backtest(days: int) -> pd.DataFrame:
    """Run spy_daily_backtest's SPYBacktest over the last `days` calendar days.

    Isolated so tests can monkey-patch it.

    Note: SPYBacktest requires at least 210 bars of warmup before producing
    results, so we always load at least 2 years of data regardless of `days`.
    The output is trimmed to only return records within the requested window.
    """
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar

    # Always load at least 2 years of SPY data so the backtest has enough
    # warmup bars (start_idx=210). The requested `days` window is applied
    # as a post-filter on the results.
    load_years = max(2.0, days / 365.0)
    loader = BacktestDataLoader()
    spy_df, vix_df = loader.load(years=load_years, source="local")
    cal = EventCalendar()
    bt = SPYBacktest(spy_df, vix_df, cal, years=load_years)
    df = bt.run()
    # Trim to the requested window
    cutoff = date.today() - timedelta(days=days)
    return df[df["date"] >= cutoff]


def transform_backtest_row(row: dict, seq: int) -> dict | None:
    """Transform one backtest-result row → TradeRecorder-schema record.

    Returns None for skipped/non-tradeable days.
    """
    if not row.get("tradeable") or row.get("play") == "skip":
        return None
    play = row.get("play", "")
    if play not in PLAY_TO_STRATEGY:
        return None
    strategy, direction = PLAY_TO_STRATEGY[play]

    # Build the record. Phase 4a uses placeholder option-pricing fields
    # (entry_price/exit_price are unknown for daily backtest — it only
    # tracks regime → outcome → fixed pnl). We populate what we can and
    # leave pricing fields null. Phase 4b's Path 1 backfill replaces these.
    sim_id = f"sim_{uuid.uuid4().hex[:8]}"
    entry_date = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
    outcome = row.get("outcome", "skip")
    pnl_dollars = float(row.get("pnl", 0))

    return {
        # Identity
        "trade_id":   sim_id,
        "ticker":     "SPY",
        "trade_type": "option_spread",
        "strategy":   strategy,
        "direction":  direction,
        "mode":       "swing",

        # Entry (placeholder — daily backtest does not track per-leg prices)
        "entry_price": None,
        "size":        1,
        "entry_date":  entry_date,
        "entry_value": None,

        # Spread fields (placeholder)
        "legs":       [],
        "max_profit": None,
        "max_loss":   None,

        # Alert link
        "alert_timestamp": None,
        "alert_score":     None,

        # Exit
        "exit_price": None,
        "exit_date":  entry_date,    # daily backtest treats each day as one-shot
        "exit_value": None,

        # Outcome
        "outcome":          outcome,
        "pnl_dollars":      pnl_dollars,
        "pnl_pct":          None,
        "pnl_per_contract": None,
        "exit_reason":      "backtest_outcome",

        # Notes
        "notes_entry": "[SEEDED-BACKFILL]",
        "notes_exit":  "",
        "lessons":     "",

        # Phase 2a tags
        "dte_bucket": "45DTE",
        "book":       "disciplined",

        # Phase 4a item 0 flag
        "simulated":  True,

        # Backtest context (extra fields for learning loop)
        "_backtest_regime": row.get("regime"),
        "_backtest_vix":    row.get("vix"),
        "_backtest_adx":    row.get("adx"),
    }


def seed_simulated_trades(days: int = 90, force: bool = False) -> int:
    """Run the backtest, transform rows, write `simulated_trades.json`.

    Returns the count of records written.
    """
    target = os.path.join(config.LOG_DIR, "simulated_trades.json")
    if os.path.exists(target) and not force:
        logger.error(
            f"{target} already exists. Re-run with --force to overwrite "
            f"(a .bak backup will be created first)."
        )
        sys.exit(2)
    if os.path.exists(target) and force:
        shutil.copy(target, target + ".bak")
        logger.info(f"Backed up existing file to {target}.bak")

    df = _run_backtest(days)
    records: list[dict] = []
    seq = 0
    for _, row in df.iterrows():
        rec = transform_backtest_row(row.to_dict(), seq)
        if rec is not None:
            records.append(rec)
            seq += 1

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        json.dump(records, f, indent=2, default=str)
    logger.info(f"Seeded {len(records)} simulated trades → {target}")
    return len(records)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days",  type=int, default=90,
                   help="Calendar days to look back (default 90 ≈ 60 trading days)")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing simulated_trades.json")
    args = p.parse_args()
    n = seed_simulated_trades(days=args.days, force=args.force)
    print(f"Seeded {n} records.")


if __name__ == "__main__":
    main()
