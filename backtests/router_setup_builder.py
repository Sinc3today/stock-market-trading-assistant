"""
backtests/router_setup_builder.py -- Historical SPYSetup factory.

Replays a target date through SPYOptionsEngine.analyze() using the same daily
+ intraday DataFrames the live scanner uses. Produces SPYSetup objects
identical (modulo indicator code drift) to what the live scanner would have
emitted on that date.

Used by backtests/intraday_router_wf.py to validate the Phase 3 entry router.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


_SPY_HISTORY_CSV = os.path.join(os.path.dirname(__file__), "spy_history.csv")
_MIN_DAILY_BARS = 30   # SPYOptionsEngine's lower bound


def load_daily_history(through_date: date) -> pd.DataFrame:
    """Daily SPY OHLCV from spy_history.csv, sliced to BARS STRICTLY BEFORE
    `through_date`. The last bar in the returned frame is the most-recent
    completed daily session before the target date — no lookahead.
    """
    df = pd.read_csv(_SPY_HISTORY_CSV, index_col=0, parse_dates=True)
    df = df[df.index < pd.Timestamp(through_date)]
    if len(df) < _MIN_DAILY_BARS:
        raise ValueError(
            f"insufficient daily history for {through_date}: "
            f"{len(df)} bars, need >= {_MIN_DAILY_BARS}"
        )
    return df
