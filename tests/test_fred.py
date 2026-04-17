"""
tests/test_fred.py — Test FRED API connection
Verifies the FRED API key works and data is accessible.

Run with:
    pytest tests/test_fred.py -v
"""

import os
import sys
import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv()


@pytest.fixture
def fred_key():
    key = os.getenv("FRED_API_KEY")
    assert key, "FRED_API_KEY not set in .env file"
    return key


def test_fred_key_is_set(fred_key):
    """FRED API key should be loaded from .env"""
    assert len(fred_key) > 10
    print(f"\n✅ FRED key loaded: {fred_key[:8]}...")


def test_fred_api_connection(fred_key):
    """Should connect to FRED and return CPI series info."""
    url = "https://api.stlouisfed.org/fred/series"
    params = {
        "series_id": "CPIAUCSL",
        "api_key":   fred_key,
        "file_type": "json",
    }
    resp = requests.get(url, params=params, timeout=10)
    assert resp.status_code == 200, f"FRED API failed: {resp.status_code}"
    data = resp.json()
    assert "seriess" in data
    title = data["seriess"][0]["title"]
    print(f"\n✅ FRED API connected — Series: {title}")


def test_fred_can_fetch_cpi_data(fred_key):
    """Should fetch recent CPI observations."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id":    "CPIAUCSL",
        "api_key":      fred_key,
        "file_type":    "json",
        "limit":        3,
        "sort_order":   "desc",
    }
    resp = requests.get(url, params=params, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    obs  = data.get("observations", [])
    assert len(obs) > 0
    print(f"\n✅ CPI data fetched — Latest: {obs[0]['date']} = {obs[0]['value']}")


def test_fred_can_fetch_jobs_data(fred_key):
    """Should fetch Non-Farm Payrolls data."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id":  "PAYEMS",
        "api_key":    fred_key,
        "file_type":  "json",
        "limit":      1,
        "sort_order": "desc",
    }
    resp = requests.get(url, params=params, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    obs  = data.get("observations", [])
    assert len(obs) > 0
    print(f"\n✅ Jobs data fetched — Latest: {obs[0]['date']} = {obs[0]['value']}K")