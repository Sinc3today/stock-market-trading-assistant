"""tests/test_dashboard_price.py -- the dashboard must always show a price.

_ticker_spot was a private re-implementation of PolygonClient.get_latest_price
with a different lookback (days_back=3 vs 5). On Tuesday 2026-09-08 -- the
first session after Labor Day -- the last trading day was Friday 09-04, four
calendar days back. The 3-day window contained no trading day at all, so SPY
and QQQ rendered as an em-dash while VIX (a different client) rendered fine.

Two copies of "what is the latest price", drifting apart. Same shape as the
13-copies problem from the 2026-09-07 audit, so the fix is the same: one
owner, and a window wide enough for the longest realistic market closure.

A price also carries WHEN it is from. Friday's close shown on Tuesday is
useful; Friday's close shown on Tuesday while looking like a live quote is
how you mirror a stale strike onto a broker.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts import web_app


class _Client:
    """Records the lookback it was asked for and replays a fixed bar set."""
    def __init__(self, bars_by_days_back):
        self.bars = bars_by_days_back
        self.asked = []

    def get_bars(self, ticker, timeframe="day", limit=200, days_back=365, **kw):
        self.asked.append(days_back)
        rows = self.bars.get(timeframe)
        if rows is None:
            return None
        cutoff = datetime.now() - timedelta(days=days_back)
        rows = [r for r in rows if r[0] >= cutoff]
        if not rows:
            return None
        return pd.DataFrame([{"close": c} for _, c in rows],
                            index=[t for t, _ in rows])


def _install(monkeypatch, client):
    import data.polygon_client as pc
    monkeypatch.setattr(pc, "PolygonClient", lambda *a, **k: client)


def test_a_four_day_weekend_still_returns_a_price(monkeypatch):
    """The reported bug: Tuesday after Labor Day, last close is 4 days back."""
    last_close = datetime.now() - timedelta(days=4)
    _install(monkeypatch, _Client({"day": [(last_close, 770.19)]}))
    assert web_app._ticker_spot("SPY") == pytest.approx(770.19)


def test_survives_the_longest_realistic_market_closure(monkeypatch):
    """Markets have closed for the better part of a week (hurricanes, 9/11).
    A dashboard that blanks out in that week is blank exactly when it matters."""
    last_close = datetime.now() - timedelta(days=6)
    _install(monkeypatch, _Client({"day": [(last_close, 770.19)]}))
    assert web_app._ticker_spot("SPY") == pytest.approx(770.19)


def test_no_data_at_all_still_returns_none(monkeypatch):
    """Widening the window must not turn 'no price' into a fabricated one."""
    _install(monkeypatch, _Client({"day": []}))
    assert web_app._ticker_spot("SPY") is None


def test_an_intraday_bar_is_preferred_over_yesterdays_close(monkeypatch):
    """Pre-market and intraday, a minute bar is closer to the truth than the
    prior daily close -- that is what 'active price at all times' means."""
    _install(monkeypatch, _Client({
        "day":  [(datetime.now() - timedelta(days=1), 770.19)],
        "1min": [(datetime.now() - timedelta(minutes=20), 769.42)],
    }))
    assert web_app._ticker_spot("SPY") == pytest.approx(769.42)


def test_a_stale_intraday_bar_is_not_preferred(monkeypatch):
    """A minute bar from days ago is not 'live' -- fall back to the close."""
    _install(monkeypatch, _Client({
        "day":  [(datetime.now() - timedelta(days=1), 770.19)],
        "1min": [(datetime.now() - timedelta(days=3), 700.00)],
    }))
    assert web_app._ticker_spot("SPY") == pytest.approx(770.19)


def test_spot_quote_reports_when_the_price_is_from(monkeypatch):
    _install(monkeypatch, _Client({
        "day":  [(datetime.now() - timedelta(days=4), 770.19)],
    }))
    q = web_app._spot_quote("SPY")
    assert q["price"] == pytest.approx(770.19)
    assert q["stale"] is True
    assert q["as_of"] is not None


def test_a_fresh_intraday_quote_is_not_marked_stale(monkeypatch):
    _install(monkeypatch, _Client({
        "day":  [(datetime.now() - timedelta(days=1), 770.19)],
        "1min": [(datetime.now() - timedelta(minutes=10), 769.42)],
    }))
    q = web_app._spot_quote("SPY")
    assert q["price"] == pytest.approx(769.42)
    assert q["stale"] is False


# ── the label ────────────────────────────────────────────────────

def test_the_subtitle_does_not_claim_live_when_the_price_is_a_stale_close():
    """The card hardcoded 'live underlying'. On the Tuesday after Labor Day
    that would have asserted Friday's close was a live quote -- a label
    stating something the data does not support."""
    from datetime import datetime, timedelta
    q = {"price": 770.19, "as_of": datetime.now() - timedelta(days=4),
         "stale": True, "source": "close"}
    sub = web_app._price_sub(q)
    assert "live underlying" not in sub.lower()   # the old unconditional claim
    assert "not live" in sub.lower()              # saying so is fine, and better
    assert "close" in sub.lower()


def test_the_subtitle_dates_a_stale_close_so_it_can_be_judged():
    from datetime import datetime, timedelta
    d = datetime.now() - timedelta(days=4)
    sub = web_app._price_sub({"price": 770.19, "as_of": d,
                              "stale": True, "source": "close"})
    assert d.strftime("%b").lower() in sub.lower() or d.strftime("%m-%d") in sub


def test_a_fresh_intraday_price_is_labelled_with_its_time():
    from datetime import datetime, timedelta
    q = {"price": 769.42, "as_of": datetime.now() - timedelta(minutes=15),
         "stale": False, "source": "intraday"}
    sub = web_app._price_sub(q)
    assert "delayed" in sub.lower() or "ET" in sub


def test_a_missing_price_says_unavailable_not_live():
    sub = web_app._price_sub({"price": None, "as_of": None,
                              "stale": True, "source": None})
    assert "live" not in sub.lower()
    assert "unavailable" in sub.lower()
