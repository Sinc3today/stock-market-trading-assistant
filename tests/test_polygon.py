"""
tests/test_polygon.py — Test the Polygon data client

Run with:
    pytest tests/test_polygon.py -v

NOTE: Requires a valid POLYGON_API_KEY in your .env file.
These tests make real API calls — use sparingly.
"""

import pytest
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.polygon_client import PolygonClient


@pytest.fixture
def client():
    return PolygonClient()


def test_get_daily_bars(client):
    """Should return a DataFrame with expected columns."""
    df = client.get_bars("AAPL", timeframe="day", limit=50)
    assert df is not None, "DataFrame should not be None"
    assert isinstance(df, pd.DataFrame), "Should return a DataFrame"
    assert len(df) > 0, "Should have at least one row"
    for col in ["open", "high", "low", "close", "volume"]:
        assert col in df.columns, f"Missing column: {col}"
    print(f"\n✅ Got {len(df)} daily bars for AAPL")
    print(df.tail(3))


def test_get_intraday_bars(client):
    """Should return intraday bars for 15min timeframe."""
    df = client.get_bars("SPY", timeframe="15min", limit=50, days_back=5)
    assert df is not None
    assert len(df) > 0
    print(f"\n✅ Got {len(df)} 15min bars for SPY")


def test_invalid_ticker(client):
    """Should return None for a fake ticker, not crash."""
    df = client.get_bars("FAKEXYZ123", timeframe="day", limit=10)
    assert df is None or len(df) == 0, "Should return None or empty for invalid ticker"
    print("\n✅ Invalid ticker handled gracefully")


def test_get_latest_price(client):
    """Should return a float price."""
    price = client.get_latest_price("AAPL")
    assert price is not None
    assert isinstance(price, float)
    assert price > 0
    print(f"\n✅ AAPL latest price: ${price:.2f}")


def test_parse_timeframe():
    """Should correctly parse all supported timeframe strings."""
    cases = {
        "day":   (1, "day"),
        "4hour": (4, "hour"),
        "15min": (15, "minute"),
        "5min":  (5, "minute"),
    }
    for tf, expected in cases.items():
        result = PolygonClient._parse_timeframe(tf)
        assert result == expected, f"Failed for {tf}: got {result}"
    print("\n✅ All timeframe mappings correct")
