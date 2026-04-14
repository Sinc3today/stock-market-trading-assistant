"""
tests/test_alpaca.py — Test Alpaca data client
Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in .env

Run with:
    pytest tests/test_alpaca.py -v
"""

import pytest
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data.alpaca_client import AlpacaClient


@pytest.fixture
def client():
    return AlpacaClient()


def test_get_15min_bars(client):
    """Should return 15min bars for SPY."""
    df = client.get_bars("SPY", timeframe="15min", limit=100, days_back=10)
    assert df is not None, "DataFrame should not be None"
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    for col in ["open", "high", "low", "close", "volume"]:
        assert col in df.columns
    print(f"\n✅ Got {len(df)} 15min bars for SPY")
    print(df.tail(3))


def test_get_5min_bars(client):
    """Should return 5min bars."""
    df = client.get_bars("AAPL", timeframe="5min", limit=50, days_back=5)
    assert df is not None
    assert len(df) > 0
    print(f"\n✅ Got {len(df)} 5min bars for AAPL")


def test_sufficient_bars_for_intraday_scanner(client):
    """Should return enough bars for the intraday scanner (need >= 15)."""
    df = client.get_bars("SPY", timeframe="15min", limit=200, days_back=10)
    assert df is not None
    assert len(df) >= 15, f"Need at least 15 bars, got {len(df)}"
    print(f"\n✅ {len(df)} bars — sufficient for intraday scanner")


def test_parse_timeframe():
    """All timeframe mappings should work."""
    cases = {
        "15min": "15Min",
        "5min":  "5Min",
        "1min":  "1Min",
        "1hour": "1Hour",
        "1day":  "1Day",
    }
    for tf, expected in cases.items():
        result = AlpacaClient._parse_timeframe(tf)
        assert result == expected
    print("\n✅ All Alpaca timeframe mappings correct")


def test_invalid_ticker_returns_none(client):
    """Invalid ticker should return None gracefully."""
    df = client.get_bars("FAKEXYZ999", timeframe="15min", limit=10, days_back=5)
    assert df is None or len(df) == 0
    print("\n✅ Invalid ticker handled gracefully")
