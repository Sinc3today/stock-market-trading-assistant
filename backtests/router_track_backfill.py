"""backtests/router_track_backfill.py -- Offline replay of cached history
through route_explain(), writing trace rows (source='backfill').

Mirrors the walk-forward's model: one evaluation per trading day at 09:45 ET,
a fresh _MockBroker per day (no cross-day dedup leak), accepted buckets
recorded forward within the day so same-day same-combo setups hit dedup.

CLI:
    python -m backtests.router_track_backfill --start 2024-01-02 --end 2024-12-31
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from loguru import logger

from backtests.router_setup_builder import build_historical_setup
from backtests.intraday_router_wf import _MockBroker
from signals.intraday_entry_router import route_explain
from learning.router_tracker import build_trace_record, write_trace

_ET = pytz.timezone("US/Eastern")


def backfill(start: date, end: date) -> int:
    """Replay [start, end] (inclusive) through route_explain; append trace rows
    to logs/learning/router_track/<day>.jsonl. Returns rows written."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:                              # Standing Rule #1: weekdays only
            try:
                setups = build_historical_setup(d)
            except ValueError as e:                      # insufficient history at range start
                logger.debug(f"backfill: skip {d}: {e}")
                setups = []
            broker = _MockBroker()                       # fresh per day
            ts = _ET.localize(datetime.combine(d, time(9, 45)))
            for setup in setups:
                trace = route_explain(setup, ts, broker)
                write_trace(build_trace_record(setup, trace, ts, "backfill"), day=d)
                n += 1
                for a in trace["accepted"]:              # record opens forward within the day
                    broker.record_open(strategy=setup.strategy, dte_bucket=a["dte_bucket"])
        d += timedelta(days=1)
    logger.info(f"backfill: {n} trace rows over {start}..{end}")
    return n


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill router decision traces")
    parser.add_argument("--start", default="2024-01-02", help="ISO date")
    parser.add_argument("--end",   default="2024-12-31", help="ISO date")
    args = parser.parse_args()
    total = backfill(date.fromisoformat(args.start), date.fromisoformat(args.end))
    logger.info(f"backfill: done, {total} rows")
