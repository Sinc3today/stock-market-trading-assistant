"""
data/alpaca_client.py — Alpaca Market Data Client
Used for intraday bars (15min, 5min) where Alpaca's free tier
provides better data than Polygon's free tier.

Polygon remains the source for daily/4H swing data.

Usage:
    from data.alpaca_client import AlpacaClient
    client = AlpacaClient()
    df = client.get_bars("AAPL", timeframe="15min", limit=100)
"""

import time
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
import pandas as pd
import config


class AlpacaClient:
    """
    Alpaca market data client for intraday bars.
    Uses the Alpaca Data API v2.
    Free tier includes historical aggregate bars.
    """

    BASE_URL = "https://data.alpaca.markets/v2"

    def __init__(self):
        if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")
        self.headers = {
            "APCA-API-KEY-ID":     config.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
        }
        logger.info("AlpacaClient initialized")

    # ─────────────────────────────────────────
    # BARS
    # ─────────────────────────────────────────

    def get_bars(
        self,
        ticker:      str,
        timeframe:   str   = "15min",
        limit:       int   = 200,
        days_back:   int   = 10,
        retries:     int   = 3,
        retry_delay: float = 10.0,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars from Alpaca.

        Args:
            ticker:    Stock symbol e.g. "AAPL"
            timeframe: "15min", "5min", "1min", "1hour", "1day"
            limit:     Max bars to return
            days_back: How far back to fetch
            retries:   Retry attempts on rate limit
            retry_delay: Seconds between retries

        Returns:
            DataFrame with open, high, low, close, volume columns
            Returns None if fetch fails.
        """
        import requests

        timeframe_str = self._parse_timeframe(timeframe)
        end   = datetime.utcnow()
        start = end - timedelta(days=days_back)

        params = {
            "timeframe": timeframe_str,
            "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit":     limit,
            "adjustment":"raw",
            "feed":      "iex",   # IEX feed works on free tier
        }

        url = f"{self.BASE_URL}/stocks/{ticker}/bars"

        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"Fetching {timeframe} bars for {ticker} from Alpaca (attempt {attempt}/{retries})")
                resp = requests.get(url, headers=self.headers, params=params, timeout=15)

                if resp.status_code == 429:
                    if attempt < retries:
                        logger.warning(f"Alpaca rate limit for {ticker} — waiting {retry_delay}s")
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.error(f"Alpaca rate limit exceeded for {ticker}")
                        return None

                if resp.status_code == 403:
                    logger.error(f"Alpaca API key invalid or insufficient permissions for {ticker}")
                    return None

                resp.raise_for_status()
                data = resp.json()
                bars = data.get("bars", [])

                if not bars:
                    logger.warning(f"No Alpaca data for {ticker} ({timeframe})")
                    return None

                df = pd.DataFrame([{
                    "timestamp": pd.to_datetime(b["t"]),
                    "open":      b["o"],
                    "high":      b["h"],
                    "low":       b["l"],
                    "close":     b["c"],
                    "volume":    b["v"],
                } for b in bars])

                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)

                logger.info(f"Alpaca: Fetched {len(df)} {timeframe} bars for {ticker}")
                return df

            except Exception as e:
                err = str(e).lower()
                if "429" in str(e) or "too many" in err:
                    if attempt < retries:
                        logger.warning(f"Alpaca rate limit for {ticker} — retrying in {retry_delay}s")
                        time.sleep(retry_delay)
                        continue
                logger.error(f"Alpaca error for {ticker}: {e}")
                return None

        return None

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Get most recent close price from Alpaca."""
        df = self.get_bars(ticker, timeframe="15min", limit=1, days_back=2)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return None

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    @staticmethod
    def _parse_timeframe(timeframe: str) -> str:
        """Convert friendly timeframe to Alpaca format."""
        mapping = {
            "1min":  "1Min",
            "5min":  "5Min",
            "15min": "15Min",
            "1hour": "1Hour",
            "4hour": "4Hour",
            "1day":  "1Day",
            "day":   "1Day",
        }
        if timeframe not in mapping:
            raise ValueError(
                f"Unknown timeframe '{timeframe}'. "
                f"Choose from: {list(mapping.keys())}"
            )
        return mapping[timeframe]