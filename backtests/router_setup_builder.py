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


from data.intraday_data import get_stock_intraday


def load_intraday_window(target_date: date) -> pd.DataFrame:
    """5-min SPY bars for `target_date`, sliced to 09:30-09:45 ET (opening
    range). Returns an empty DataFrame when no data is available — the caller
    treats that as 'skip this day, no signal.'

    Polygon list_aggs returns bars indexed in UTC; we keep them UTC here and
    let downstream tz conversion happen at the engine boundary.
    """
    df = get_stock_intraday("SPY", 5, "minute", target_date, target_date)
    if df.empty:
        return df
    # get_stock_intraday returns a tz-naive UTC index (data/intraday_data.py
    # uses pd.to_datetime(unit="ms") which is tz-naive). Localize to UTC so
    # the slice bounds (also UTC) compare cleanly across DST.
    if df.index.tz is None:
        df = df.tz_localize("UTC")
    # ET 09:30-09:44:59 == UTC 13:30-13:44:59 (EDT) or 14:30-14:44:59 (EST).
    # We slice in UTC against the actual session date — Polygon returns the
    # session date's bars whichever DST half we're in.
    et_open  = pd.Timestamp(f"{target_date.isoformat()} 09:30:00", tz="US/Eastern")
    et_or_end = pd.Timestamp(f"{target_date.isoformat()} 09:45:00", tz="US/Eastern")
    utc_open  = et_open.tz_convert("UTC")
    utc_or_end = et_or_end.tz_convert("UTC")
    return df[(df.index >= utc_open) & (df.index < utc_or_end)]
