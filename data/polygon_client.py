"""
data/polygon_client.py — Polygon.io data fetcher
Handles all market data requests: bars, volume, ticker details.
Includes retry logic for rate limit (429) responses.

Usage:
    from data.polygon_client import PolygonClient
    client = PolygonClient()
    df = client.get_bars("AAPL", timeframe="day", limit=200)
"""

import time
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
import pandas as pd
from polygon import RESTClient
import config


class PolygonClient:
    """
    Wrapper around the Polygon.io REST API.
    Provides clean DataFrames for use by indicator modules.
    Automatically retries on rate limit (429) responses.
    """

    def __init__(self):
        if not config.POLYGON_API_KEY:
            raise ValueError("POLYGON_API_KEY is not set in your .env file")
        self.client = RESTClient(api_key=config.POLYGON_API_KEY)
        logger.info("PolygonClient initialized")

    # ─────────────────────────────────────────
    # BARS (OHLCV)
    # ─────────────────────────────────────────

    def get_bars(
        self,
        ticker:    str,
        timeframe: str = "day",
        limit:     int = 200,
        days_back: int = 365,
        retries:   int = 3,
        retry_delay: float = 15.0,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars for a ticker.
        Retries up to `retries` times on rate limit errors.

        Args:
            ticker:      Stock symbol e.g. "AAPL"
            timeframe:   "day", "4hour", "15min", "5min", "1min"
            limit:       Max number of bars to return
            days_back:   How far back to fetch data
            retries:     Number of retry attempts on rate limit
            retry_delay: Seconds to wait between retries

        Returns:
            DataFrame with columns: open, high, low, close, volume, timestamp
            Returns None if fetch fails after all retries.
        """
        multiplier, span = self._parse_timeframe(timeframe)
        end_date   = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"Fetching {timeframe} bars for {ticker} "
                             f"(attempt {attempt}/{retries})...")
                aggs = self.client.get_aggs(
                    ticker=ticker,
                    multiplier=multiplier,
                    timespan=span,
                    from_=start_date.strftime("%Y-%m-%d"),
                    to=end_date.strftime("%Y-%m-%d"),
                    limit=limit,
                    adjusted=True,
                )

                if not aggs:
                    logger.warning(f"No data returned for {ticker} ({timeframe})")
                    return None

                df = pd.DataFrame([{
                    "timestamp": datetime.fromtimestamp(a.timestamp / 1000),
                    "open":   a.open,
                    "high":   a.high,
                    "low":    a.low,
                    "close":  a.close,
                    "volume": a.volume,
                } for a in aggs])

                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)

                logger.info(f"Fetched {len(df)} bars for {ticker} ({timeframe})")
                return df

            except Exception as e:
                err_str = str(e).lower()

                # Rate limit hit — wait and retry
                if "429" in str(e) or "too many" in err_str:
                    if attempt < retries:
                        logger.warning(
                            f"Rate limit hit for {ticker} — "
                            f"waiting {retry_delay}s before retry "
                            f"({attempt}/{retries})"
                        )
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.error(
                            f"Rate limit exceeded for {ticker} after "
                            f"{retries} attempts — try again in a minute"
                        )
                        return None

                # Other error — don't retry
                logger.error(f"Error fetching bars for {ticker}: {e}")
                return None

        return None

    # ─────────────────────────────────────────
    # TICKER DETAILS
    # ─────────────────────────────────────────

    def get_ticker_details(self, ticker: str) -> Optional[dict]:
        """Return Polygon ticker details dict, or None on error."""
        try:
            details = self.client.get_ticker_details(ticker)
            logger.debug(f"Fetched details for {ticker}")
            return details
        except Exception as e:
            logger.error(f"Error fetching details for {ticker}: {e}")
            return None

    # ─────────────────────────────────────────
    # LATEST PRICE
    # ─────────────────────────────────────────

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Return the most recent closing price for a ticker."""
        df = self.get_bars(ticker, timeframe="day", limit=1, days_back=5)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return None

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    @staticmethod
    def _parse_timeframe(timeframe: str) -> tuple[int, str]:
        mapping = {
            "day":   (1, "day"),
            "4hour": (4, "hour"),
            "1hour": (1, "hour"),
            "15min": (15, "minute"),
            "5min":  (5, "minute"),
            "1min":  (1, "minute"),
        }
        if timeframe not in mapping:
            raise ValueError(
                f"Unknown timeframe '{timeframe}'. "
                f"Choose from: {list(mapping.keys())}"
            )
        return mapping[timeframe]