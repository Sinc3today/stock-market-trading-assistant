"""
data/vix_client.py — VIX Data Client

Fetches current and historical VIX values.

Primary source:  Polygon.io  → ticker "I:VIX" (requires Stocks Starter+)
Fallback source: CBOE CSV    → free, updated daily after market close

The fallback matters because:
    - Free Polygon tier may not include index data
    - We need 52-week high/low to compute IV Rank (used by IVRClient)

Usage:
    from data.vix_client import VIXClient
    client = VIXClient()
    vix = client.get_current()           # float
    hist = client.get_history(days=252)  # DataFrame with 'close' column

Run standalone:
    python -m data.vix_client
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import io
import time
from datetime import datetime, timedelta, date
from typing import Optional

import pandas as pd
import requests
from loguru import logger

import config

# ── CBOE CSV endpoint (free, no auth) ────────────────────────
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

# ── Polygon index ticker ─────────────────────────────────────
POLYGON_VIX_TICKER = "I:VIX"

# ── Cache settings ───────────────────────────────────────────
_CACHE_TTL_SECONDS = 300   # Re-fetch at most every 5 minutes
_cache: dict = {"vix": None, "fetched_at": None, "df": None, "df_at": None}


class VIXClient:
    """
    Retrieves VIX spot and historical data.
    Tries Polygon first; falls back to CBOE CSV automatically.
    Caches results in-process to avoid hammering APIs.
    """

    def __init__(self):
        self.polygon_key = config.POLYGON_API_KEY
        logger.info("VIXClient initialized")

    # ─────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────

    def get_current(self) -> float:
        """
        Return today's VIX close (or most recent available).
        Cached for 5 minutes to avoid repeated API calls.

        Returns:
            VIX as float (e.g. 14.83). Returns 20.0 as a safe fallback
            if all data sources fail — 20.0 is mid-range, won't trigger
            either extreme branch in RegimeDetector.
        """
        # ── In-process cache ──────────────────────────────────
        if _cache["vix"] and _cache["fetched_at"]:
            age = (datetime.now() - _cache["fetched_at"]).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                logger.debug(f"VIX from cache: {_cache['vix']} (age {age:.0f}s)")
                return _cache["vix"]

        # ── Try Polygon first ─────────────────────────────────
        vix = self._fetch_polygon_latest()
        if vix is not None:
            self._update_cache(vix)
            return vix

        # ── Fall back to CBOE ─────────────────────────────────
        vix = self._fetch_cboe_latest()
        if vix is not None:
            self._update_cache(vix)
            return vix

        # ── Last resort: safe default ─────────────────────────
        logger.warning("VIX unavailable from all sources — using fallback 20.0")
        return 20.0

    def get_history(self, days: int = 252) -> Optional[pd.DataFrame]:
        """
        Return daily VIX closes for the last N calendar days.
        DataFrame index = date, single column = 'close'.
        Used by IVRClient to compute 52-week IV Range.

        Returns None if data unavailable.
        """
        # ── In-process cache ──────────────────────────────────
        if _cache["df"] is not None and _cache["df_at"]:
            age = (datetime.now() - _cache["df_at"]).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                logger.debug("VIX history from cache")
                return _cache["df"]

        # ── Try Polygon first ─────────────────────────────────
        df = self._fetch_polygon_history(days)
        if df is not None and len(df) >= 30:
            _cache["df"]    = df
            _cache["df_at"] = datetime.now()
            return df

        # ── Fall back to CBOE ─────────────────────────────────
        df = self._fetch_cboe_history(days)
        if df is not None:
            _cache["df"]    = df
            _cache["df_at"] = datetime.now()
            return df

        logger.warning("VIX history unavailable from all sources")
        return None

    # ─────────────────────────────────────────
    # POLYGON FETCHERS
    # ─────────────────────────────────────────

    def _fetch_polygon_latest(self) -> Optional[float]:
        """Fetch latest VIX close from Polygon I:VIX."""
        if not self.polygon_key:
            return None
        try:
            from polygon import RESTClient
            client = RESTClient(api_key=self.polygon_key)
            end   = datetime.now()
            start = end - timedelta(days=5)
            aggs  = client.get_aggs(
                ticker     = POLYGON_VIX_TICKER,
                multiplier = 1,
                timespan   = "day",
                from_      = start.strftime("%Y-%m-%d"),
                to         = end.strftime("%Y-%m-%d"),
                limit      = 5,
                adjusted   = False,
            )
            if aggs:
                vix = float(aggs[-1].close)
                logger.info(f"VIX from Polygon: {vix}")
                return vix
        except Exception as e:
            logger.debug(f"Polygon VIX fetch failed (may need Starter+ plan): {e}")
        return None

    def _fetch_polygon_history(self, days: int) -> Optional[pd.DataFrame]:
        """Fetch VIX daily history from Polygon."""
        if not self.polygon_key:
            return None
        try:
            from polygon import RESTClient
            client = RESTClient(api_key=self.polygon_key)
            end    = datetime.now()
            start  = end - timedelta(days=days + 30)  # buffer for weekends
            aggs   = client.get_aggs(
                ticker     = POLYGON_VIX_TICKER,
                multiplier = 1,
                timespan   = "day",
                from_      = start.strftime("%Y-%m-%d"),
                to         = end.strftime("%Y-%m-%d"),
                limit      = days + 60,
                adjusted   = False,
            )
            if not aggs:
                return None
            df = pd.DataFrame([{
                "date":  datetime.fromtimestamp(a.timestamp / 1000).date(),
                "close": float(a.close),
            } for a in aggs])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            logger.info(f"VIX history from Polygon: {len(df)} days")
            return df
        except Exception as e:
            logger.debug(f"Polygon VIX history failed: {e}")
        return None

    # ─────────────────────────────────────────
    # CBOE FALLBACK FETCHERS
    # ─────────────────────────────────────────

    def _fetch_cboe_latest(self) -> Optional[float]:
        """Parse CBOE CSV and return most recent VIX close."""
        df = self._fetch_cboe_df()
        if df is not None and len(df) > 0:
            vix = float(df["close"].iloc[-1])
            logger.info(f"VIX from CBOE CSV: {vix}")
            return vix
        return None

    def _fetch_cboe_history(self, days: int) -> Optional[pd.DataFrame]:
        """Return last N days of VIX from CBOE CSV."""
        df = self._fetch_cboe_df()
        if df is None:
            return None
        cutoff = date.today() - timedelta(days=days)
        df = df[df.index >= cutoff]
        logger.info(f"VIX history from CBOE CSV: {len(df)} days")
        return df

    @staticmethod
    def _fetch_cboe_df() -> Optional[pd.DataFrame]:
        """
        Download and parse the CBOE VIX history CSV.
        Format: DATE, OPEN, HIGH, LOW, CLOSE  (columns may vary by year)
        """
        try:
            resp = requests.get(CBOE_VIX_URL, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))

            # Normalise column names — CBOE sometimes uses "DATE" or "Date"
            df.columns = [c.strip().lower() for c in df.columns]
            if "date" not in df.columns or "close" not in df.columns:
                logger.warning(f"CBOE CSV unexpected columns: {df.columns.tolist()}")
                return None

            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            df["date"] = df["date"].dt.date
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            df = df[["close"]].copy()
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.dropna()
            return df

        except Exception as e:
            logger.error(f"CBOE VIX CSV fetch failed: {e}")
            return None

    # ─────────────────────────────────────────
    # CACHE HELPER
    # ─────────────────────────────────────────

    @staticmethod
    def _update_cache(vix: float):
        _cache["vix"]        = vix
        _cache["fetched_at"] = datetime.now()


# ─────────────────────────────────────────
# STANDALONE SMOKE TEST
# ─────────────────────────────────────────

if __name__ == "__main__":
    client = VIXClient()

    print("\n── VIX current ──")
    current = client.get_current()
    print(f"  VIX = {current:.2f}")

    print("\n── VIX history (last 10 rows) ──")
    hist = client.get_history(days=30)
    if hist is not None:
        print(hist.tail(10).to_string())
        print(f"\n  52w high (sample): {hist['close'].max():.2f}")
        print(f"  52w low  (sample): {hist['close'].min():.2f}")
    else:
        print("  No history available")
