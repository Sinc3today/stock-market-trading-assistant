"""
data/intraday_data.py -- Cached, fully-paginated intraday stock bars.

PolygonClient.get_bars uses single-page get_aggs (capped ~50k, returns only
the oldest slice of a long window — that's why a 2yr request came back with
~3 months). For multi-year intraday backtests we need list_aggs, which
auto-paginates the whole range. This module wraps that and caches the result
to parquet under backtests/.cache/ so repeated backtests don't re-hit the API.

Pairs with data/options_history.py (option aggregates) to feed the real-priced
0DTE/1DTE backtest.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "backtests", ".cache")


def _cache_path(ticker: str, multiplier: int, timespan: str,
                from_s: str, to_s: str) -> str:
    name = f"{ticker.upper()}_{multiplier}{timespan}_{from_s}_{to_s}.parquet"
    return os.path.join(_CACHE_DIR, name)


def get_stock_intraday(ticker: str, multiplier: int, timespan: str,
                       from_date, to_date, client=None,
                       use_cache: bool = True) -> pd.DataFrame:
    """
    Fully-paginated OHLCV bars for `ticker` over [from_date, to_date],
    cached to parquet. timespan: 'minute' | 'hour' | 'day'.

    Returns a DataFrame indexed by timestamp (open/high/low/close/volume),
    or an empty DataFrame on failure.
    """
    from_s = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
    to_s   = to_date.isoformat()   if hasattr(to_date, "isoformat")   else str(to_date)
    path   = _cache_path(ticker, multiplier, timespan, from_s, to_s)

    if use_cache and os.path.exists(path):
        try:
            return pd.read_parquet(path)
        except Exception as e:
            logger.warning(f"intraday_data: cache read failed ({path}): {e}")

    if client is None:
        from polygon import RESTClient
        client = RESTClient(config.POLYGON_API_KEY)

    try:
        bars = list(client.list_aggs(ticker.upper(), multiplier, timespan,
                                     from_s, to_s, limit=50000))
    except Exception as e:
        logger.warning(f"intraday_data: list_aggs failed for {ticker}: {e}")
        return pd.DataFrame()
    if not bars:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "timestamp": pd.to_datetime(getattr(b, "timestamp", None), unit="ms"),
        "open":   getattr(b, "open", None),
        "high":   getattr(b, "high", None),
        "low":    getattr(b, "low", None),
        "close":  getattr(b, "close", None),
        "volume": getattr(b, "volume", None),
    } for b in bars]).set_index("timestamp").sort_index()

    if use_cache:
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            df.to_parquet(path)
        except Exception as e:
            logger.warning(f"intraday_data: cache write failed ({path}): {e}")

    logger.info(f"intraday_data: {ticker} {multiplier}{timespan} "
                f"{from_s}->{to_s}: {len(df)} bars")
    return df
