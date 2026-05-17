"""
tests/test_earnings_calendar.py -- EarningsCalendar with mocked Polygon
and an isolated watchlist + cache directory.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.earnings_calendar import EarningsCalendar


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


@pytest.fixture
def watchlist_file(tmp_path):
    """Minimal watchlist with three tickers; union of all lists."""
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps({
        "swing":           ["AAPL", "MSFT"],
        "intraday":        ["AAPL", "NVDA"],   # AAPL dedup'd, NVDA added
        "options_enabled": ["MSFT"],
    }))
    return str(path)


class FakeDetails:
    """Mimics what polygon.get_ticker_details() returns — an object with
    a `next_earnings_date` attribute."""
    def __init__(self, next_earnings_date):
        self.next_earnings_date = next_earnings_date


class FakePolygon:
    """Fixed mapping of ticker → next_earnings_date string (or None)."""
    def __init__(self, earnings: dict, fail: set | None = None):
        self.earnings = earnings
        self.fail = fail or set()
        self.calls = 0

    def get_ticker_details(self, ticker):
        self.calls += 1
        if ticker in self.fail:
            raise RuntimeError("polygon down")
        if ticker not in self.earnings:
            return FakeDetails(next_earnings_date=None)
        return FakeDetails(next_earnings_date=self.earnings[ticker])


def _ec(polygon, watchlist_path, **kwargs):
    return EarningsCalendar(
        polygon_client = polygon,
        watchlist_path = watchlist_path,
        **kwargs,
    )


# ─────────────────────────────────────────
# Watchlist parsing
# ─────────────────────────────────────────

def test_watchlist_unions_all_lists(iso_logs, watchlist_file):
    ec = _ec(FakePolygon({}), watchlist_file)
    assert ec._load_watchlist() == ["AAPL", "MSFT", "NVDA"]


def test_watchlist_missing_returns_empty(iso_logs):
    ec = _ec(FakePolygon({}), watchlist_path="/nonexistent/path.json")
    assert ec._load_watchlist() == []


# ─────────────────────────────────────────
# Refresh + caching
# ─────────────────────────────────────────

def test_refresh_fetches_per_ticker(iso_logs, watchlist_file):
    today  = date.today()
    in_3   = (today + timedelta(days=3)).isoformat()
    in_10  = (today + timedelta(days=10)).isoformat()
    poly   = FakePolygon({"AAPL": in_3, "MSFT": in_10})  # NVDA has no date
    ec     = _ec(poly, watchlist_file)
    out    = ec.get_upcoming(days=30)

    assert poly.calls == 3                           # one call per watchlist ticker
    tickers = [e["ticker"] for e in out]
    assert tickers == ["AAPL", "MSFT"]               # sorted by days_away
    assert out[0]["days_away"] == 3
    assert out[1]["days_away"] == 10


def test_get_upcoming_filters_by_window(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon({
        "AAPL": (today + timedelta(days=3)).isoformat(),
        "MSFT": (today + timedelta(days=20)).isoformat(),
        "NVDA": (today + timedelta(days=8)).isoformat(),
    })
    ec = _ec(poly, watchlist_file)
    out = ec.get_upcoming(days=14)
    tickers = [e["ticker"] for e in out]
    assert tickers == ["AAPL", "NVDA"]   # MSFT (20d) excluded


def test_past_dates_excluded(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon({
        "AAPL": (today - timedelta(days=5)).isoformat(),  # past
        "MSFT": (today + timedelta(days=2)).isoformat(),
    })
    ec = _ec(poly, watchlist_file)
    out = ec.get_upcoming(days=14)
    assert [e["ticker"] for e in out] == ["MSFT"]


def test_cache_skips_second_refresh(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon({"AAPL": (today + timedelta(days=3)).isoformat()})
    ec    = _ec(poly, watchlist_file)
    ec.get_upcoming(days=14)
    calls_after_first = poly.calls
    # Second call within TTL — should hit cache, no new polygon hits
    ec.get_upcoming(days=14)
    assert poly.calls == calls_after_first


def test_refresh_true_bypasses_cache(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon({"AAPL": (today + timedelta(days=3)).isoformat()})
    ec    = _ec(poly, watchlist_file)
    ec.get_upcoming(days=14)
    before = poly.calls
    ec.get_upcoming(days=14, refresh=True)
    assert poly.calls > before


def test_refresh_handles_polygon_exception(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon(
        earnings={"AAPL": (today + timedelta(days=3)).isoformat()},
        fail={"MSFT"},   # MSFT raises; AAPL + NVDA fine
    )
    ec = _ec(poly, watchlist_file)
    out = ec.get_upcoming(days=14)
    # AAPL succeeds despite MSFT failure; NVDA has no date so dropped
    assert [e["ticker"] for e in out] == ["AAPL"]


def test_no_polygon_returns_empty(iso_logs, watchlist_file):
    ec = EarningsCalendar(polygon_client=None, watchlist_path=watchlist_file)
    assert ec.get_upcoming(days=14) == []


# ─────────────────────────────────────────
# Single-ticker + today-tomorrow helpers
# ─────────────────────────────────────────

def test_get_for_ticker(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon({"AAPL": (today + timedelta(days=4)).isoformat()})
    ec    = _ec(poly, watchlist_file)
    out = ec.get_for_ticker("aapl")          # lowercase OK
    assert out and out["days_away"] == 4
    assert ec.get_for_ticker("NVDA") is None  # NVDA had no date


def test_get_today_and_tomorrow(iso_logs, watchlist_file):
    today = date.today()
    poly  = FakePolygon({
        "AAPL": today.isoformat(),                          # today (0d)
        "MSFT": (today + timedelta(days=1)).isoformat(),    # tomorrow (1d)
        "NVDA": (today + timedelta(days=5)).isoformat(),    # too far
    })
    ec = _ec(poly, watchlist_file)
    out = ec.get_today_and_tomorrow()
    assert sorted(e["ticker"] for e in out) == ["AAPL", "MSFT"]


# ─────────────────────────────────────────
# Date parsing edge cases
# ─────────────────────────────────────────

def test_handles_date_object_from_polygon(iso_logs, watchlist_file):
    """Some Polygon SDK versions return a date object, not a string."""
    today = date.today()
    poly  = FakePolygon({"AAPL": today + timedelta(days=3)})    # raw date obj
    ec    = _ec(poly, watchlist_file)
    out   = ec.get_upcoming(days=14)
    assert out[0]["earnings_date"] == (today + timedelta(days=3)).isoformat()


def test_handles_malformed_date_string(iso_logs, watchlist_file):
    poly  = FakePolygon({"AAPL": "not-a-date"})
    ec    = _ec(poly, watchlist_file)
    out   = ec.get_upcoming(days=14)
    assert out == []
