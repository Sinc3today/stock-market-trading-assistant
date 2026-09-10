"""tests/test_etf_earnings_skip.py -- do not ask an ETF for its earnings date.

The watchlist mixes ETFs (SPY, QQQ, IWM) with single names, and the earnings
refresh asked all of them. ETFs have no earnings, so yfinance answers with
HTTP 404 "No fundamentals data found for symbol: SPY" -- and logs that itself,
at ERROR, before our code sees the result. A try/except cannot quiet it; the
stdlib logging bridge forwards it to app.log, loop_health greps ERROR lines,
and the user gets paged.

earnings_calendar's own docstring already said "ETFs return an empty dict", so
the knowledge was present and simply unused. Three guaranteed-useless network
calls per refresh, each producing an ERROR line, for a question with no answer.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.earnings_calendar import EarningsCalendar


def _cal(tmp_path, tickers, asked):
    import json
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"swing": tickers}))

    def fetcher(t):
        asked.append(t)
        return "2026-10-15"

    c = EarningsCalendar(fetcher=fetcher)
    c.watchlist_path = str(wl)
    return c


def test_etfs_are_never_asked(tmp_path):
    asked = []
    _cal(tmp_path, ["SPY", "QQQ", "IWM", "AAPL"], asked)._refresh()
    assert "SPY" not in asked and "QQQ" not in asked and "IWM" not in asked


def test_single_names_are_still_asked(tmp_path):
    asked = []
    _cal(tmp_path, ["SPY", "AAPL", "NVDA"], asked)._refresh()
    assert asked == ["AAPL", "NVDA"]


def test_the_skip_is_case_insensitive(tmp_path):
    asked = []
    _cal(tmp_path, ["spy", "aapl"], asked)._refresh()
    assert not any(t.upper() == "SPY" for t in asked)


def test_an_all_etf_watchlist_makes_no_calls_at_all(tmp_path):
    asked = []
    out = _cal(tmp_path, ["SPY", "QQQ"], asked)._refresh()
    assert asked == []
    assert out == []


def test_the_skip_list_names_why_each_entry_is_on_it():
    """A bare list of tickers rots. Each entry carries its reason."""
    from data.earnings_calendar import NO_EARNINGS_TICKERS
    assert {"SPY", "QQQ", "IWM"} <= set(NO_EARNINGS_TICKERS)
    for reason in NO_EARNINGS_TICKERS.values():
        assert reason and isinstance(reason, str)
