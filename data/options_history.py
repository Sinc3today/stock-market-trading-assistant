"""
data/options_history.py -- Historical option price aggregates (Polygon paid).

OptionsChain (the live snapshot) can't backtest -- it only knows *now*. This
module fetches HISTORICAL option OHLCV for a specific contract via Polygon's
list_aggs, which the paid Options Starter tier provides:

    - daily bars over a contract's life, and
    - intraday (e.g. 5-min) bars, INCLUDING the 0DTE expiry session.

That unlocks real-priced backtests for the short-DTE tracks (0DTE/1DTE) and
lets us replace / sanity-check the Black-Scholes marks the daily realistic
backtest currently uses (BS is unreliable for 0DTE near expiry).

Pure ticker construction is separate + testable; the network fetch returns an
empty DataFrame on any failure so callers degrade gracefully.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config


def option_ticker(underlying: str, expiry: date, cp: str, strike: float) -> str:
    """
    Build the OCC-style Polygon option ticker.

        O:{UND}{YYMMDD}{C|P}{strike*1000, zero-padded to 8 digits}

    e.g. option_ticker("SPY", date(2024,8,16), "C", 550) ->
         "O:SPY240816C00550000"
    """
    cp_c = cp.strip().upper()[0]
    if cp_c not in ("C", "P"):
        raise ValueError(f"cp must be call/put, got {cp!r}")
    strike_int = int(round(float(strike) * 1000))
    return f"O:{underlying.upper()}{expiry:%y%m%d}{cp_c}{strike_int:08d}"


class OptionsHistory:
    """Historical option aggregates via Polygon list_aggs (paid tier)."""

    def __init__(self, client=None, api_key: str | None = None):
        self._client  = client
        self._api_key = api_key or config.POLYGON_API_KEY

    def _ensure_client(self):
        if self._client is None:
            from polygon import RESTClient
            self._client = RESTClient(self._api_key)
        return self._client

    def get_aggs(self, contract: str, multiplier: int, timespan: str,
                 from_date, to_date, limit: int = 50000) -> pd.DataFrame:
        """
        Fetch OHLCV bars for one option contract. timespan: 'day' | 'minute'.
        Returns a DataFrame indexed by timestamp with open/high/low/close/
        volume, or an empty DataFrame on any failure.
        """
        f = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        t = to_date.isoformat()   if hasattr(to_date, "isoformat")   else str(to_date)
        try:
            bars = list(self._ensure_client().list_aggs(
                contract, multiplier, timespan, f, t, limit=limit,
            ))
        except Exception as e:
            logger.warning(f"OptionsHistory: list_aggs failed for {contract}: {e}")
            return pd.DataFrame()
        if not bars:
            return pd.DataFrame()
        rows = [{
            "timestamp": pd.to_datetime(getattr(b, "timestamp", None), unit="ms"),
            "open":   getattr(b, "open", None),
            "high":   getattr(b, "high", None),
            "low":    getattr(b, "low", None),
            "close":  getattr(b, "close", None),
            "volume": getattr(b, "volume", None),
        } for b in bars]
        df = pd.DataFrame(rows).set_index("timestamp").sort_index()
        return df

    def leg_close(self, underlying: str, expiry: date, cp: str, strike: float,
                  on: date) -> float | None:
        """Real closing price of one option leg on a given date (daily bar).
        Returns None if no bar exists."""
        contract = option_ticker(underlying, expiry, cp, strike)
        df = self.get_aggs(contract, 1, "day", on, on)
        if df.empty:
            return None
        return float(df["close"].iloc[-1])
