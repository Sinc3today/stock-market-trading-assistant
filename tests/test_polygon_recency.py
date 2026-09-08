"""tests/test_polygon_recency.py -- limit=N must give the NEWEST bars.

Polygon's get_aggs returns ascending order and `limit` truncates from the
FRONT, so limit=1 over a 14-day window returned a bar from 11 days ago while
every caller took .iloc[-1] and called it "live".

Verified against the live API on 2026-09-07:
    limit=1  days_back=14  ->  2026-08-23  763.47   (11 days stale)
    limit=10 days_back=14  ->  2026-09-03  770.19   (correct)

It bit exactly where it hurt: ivr_client and _fetch_spot (limit=1,
days_back=5) were returning Sep 1 instead of Sep 3 — 0.65% off, and IVR feeds
the regime classifier. The stop watchdog and the copilot's "live underlying"
survived only because their window happens to be 3 days.
"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.polygon_client import PolygonClient


def _agg(day: datetime, close: float):
    a = MagicMock()
    a.timestamp = int(day.timestamp() * 1000)
    a.open = a.high = a.low = a.close = close
    a.volume = 1_000_000
    return a


def test_request_asks_for_the_newest_bars():
    """sort='desc' is what makes `limit` truncate from the RIGHT end."""
    c = PolygonClient()
    base = datetime(2026, 9, 3)
    fake = [_agg(base - timedelta(days=i), 770 - i) for i in range(3)]
    with patch.object(c, "client") as api:
        api.get_aggs.return_value = fake
        c.get_bars("SPY", "day", limit=3, days_back=14)
        kwargs = api.get_aggs.call_args.kwargs
        assert kwargs.get("sort") == "desc", \
            "without sort=desc, limit truncates the NEWEST bars away"


def test_returned_frame_is_still_oldest_to_newest():
    """Callers do .iloc[-1] for 'latest' and indicators need ascending order,
    so a desc request must be re-sorted before it is handed back."""
    c = PolygonClient()
    base = datetime(2026, 9, 3)
    desc = [_agg(base - timedelta(days=i), 770 - i) for i in range(3)]
    with patch.object(c, "client") as api:
        api.get_aggs.return_value = desc
        df = c.get_bars("SPY", "day", limit=3, days_back=14)
    assert df is not None and len(df) == 3
    assert list(df.index) == sorted(df.index), "index must ascend"
    assert float(df["close"].iloc[-1]) == 770.0, "iloc[-1] must be the NEWEST bar"


def test_a_narrow_limit_still_returns_the_latest_close():
    """The exact failing shape: limit=1 over a wide window."""
    c = PolygonClient()
    base = datetime(2026, 9, 3)
    with patch.object(c, "client") as api:
        api.get_aggs.return_value = [_agg(base, 770.19)]
        df = c.get_bars("SPY", "day", limit=1, days_back=14)
    assert float(df["close"].iloc[-1]) == pytest.approx(770.19)
