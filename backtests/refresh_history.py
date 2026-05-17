"""
backtests/refresh_history.py -- Refresh backtests/spy_history.csv from Polygon.

With Stocks Starter, Polygon serves the full 5-year SPY daily window in a
single get_bars() call. This replaces the yfinance-based `download_spy.py`
for users on a paid Polygon tier.

Usage:
    python -m backtests.refresh_history                # default 5 years
    python -m backtests.refresh_history --years 3
    python -m backtests.refresh_history --dry-run      # don't write

The script prints a diff line so the user can see what changed:
    "Updated spy_history.csv: 1,261 → 1,263 bars (+2 new)"

Falls back nothing intentionally — if Polygon fetch fails, prints a
friendly error and exits 1 (the existing download_spy.py is the
yfinance fallback).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config
from data.polygon_client import PolygonClient


CSV_PATH = os.path.join("backtests", "spy_history.csv")


def fetch_polygon_spy(years: int = 5) -> pd.DataFrame | None:
    """Pull SPY daily bars from Polygon for the last `years` years."""
    client = PolygonClient()
    df = client.get_bars(
        "SPY",
        timeframe = config.SWING_PRIMARY_TIMEFRAME,
        limit     = years * 260 + 100,
        days_back = years * 365 + 60,
    )
    if df is None or len(df) == 0:
        return None
    # Normalize to date index + lowercased OHLCV columns expected by the
    # backtest loader (matches the existing download_spy.py output shape).
    df = df.copy()
    df.index = pd.to_datetime(df.index).date
    df       = df.sort_index()
    df.columns = [c.lower() for c in df.columns]
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep]


def diff_against_existing(new_df: pd.DataFrame, csv_path: str = CSV_PATH) -> dict:
    """
    Compare `new_df` against the on-disk CSV and return a small summary
    dict: {old_n, new_n, added, removed}.
    """
    if not os.path.exists(csv_path):
        return {"old_n": 0, "new_n": len(new_df),
                "added": len(new_df), "removed": 0}
    try:
        old = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        old.index = pd.to_datetime(old.index).date
    except (OSError, pd.errors.ParserError) as e:
        logger.warning(f"refresh_history: existing CSV unreadable ({e}); treating as empty")
        return {"old_n": 0, "new_n": len(new_df),
                "added": len(new_df), "removed": 0}

    old_dates = set(old.index)
    new_dates = set(new_df.index)
    return {
        "old_n":   len(old),
        "new_n":   len(new_df),
        "added":   len(new_dates - old_dates),
        "removed": len(old_dates - new_dates),
    }


def write_csv(df: pd.DataFrame, csv_path: str = CSV_PATH) -> None:
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    df.to_csv(csv_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Refresh spy_history.csv from Polygon")
    p.add_argument("--years",   type=int, default=5)
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch + diff but don't write the CSV")
    args = p.parse_args(argv)

    print(f"▶ Fetching {args.years}y SPY daily from Polygon …")
    df = fetch_polygon_spy(years=args.years)
    if df is None:
        print("✗ Polygon fetch returned no data. "
              "Check POLYGON_API_KEY and your plan, "
              "or run the yfinance fallback: python download_spy.py")
        return 1

    delta = diff_against_existing(df)
    arrow = "→" if not args.dry_run else "(dry-run)"
    dropped_str = f", -{delta['removed']} dropped" if delta["removed"] else ""
    print(
        f"  spy_history.csv {arrow} "
        f"{delta['old_n']:,} → {delta['new_n']:,} bars "
        f"(+{delta['added']} new{dropped_str})"
    )
    print(f"  date range: {df.index[0]} → {df.index[-1]}")

    if args.dry_run:
        print("  (dry-run; CSV not written)")
        return 0

    write_csv(df)
    print(f"✓ Wrote {len(df):,} bars to {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
