"""
tests/test_earnings_history.py -- EarningsHistory reaction stats.

Uses injected date/bars fetchers so no network calls are made.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.earnings_history import EarningsHistory


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _make_bars(rows: list[tuple[date, float]]) -> pd.DataFrame:
    """Build a daily-bars DataFrame from [(date, close), ...]."""
    df = pd.DataFrame([
        {"timestamp": datetime(d.year, d.month, d.day), "close": c}
        for d, c in rows
    ])
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


# ── CLASSIFY ──────────────────────────────────────────

def test_classify_calm_normal_volatile():
    assert EarningsHistory._classify(0.8) == "calm"
    assert EarningsHistory._classify(2.5) == "normal"
    assert EarningsHistory._classify(5.0) == "volatile"
    # Boundary checks: CALM_MAX=1.5, NORMAL_MAX=3.5
    assert EarningsHistory._classify(1.5) == "normal"     # >= CALM_MAX
    assert EarningsHistory._classify(3.5) == "volatile"   # >= NORMAL_MAX


# ── NEXT-DAY MOVE ─────────────────────────────────────

def test_next_day_move_exact_date_match():
    bars = _make_bars([
        (date(2026, 5, 13), 100.0),
        (date(2026, 5, 14), 100.0),  # earnings day
        (date(2026, 5, 15), 102.0),  # next-day close → +2%
    ])
    assert EarningsHistory._next_day_move(bars, date(2026, 5, 14)) == pytest.approx(2.0)


def test_next_day_move_uses_prior_day_when_exact_missing():
    """Earnings printed on a market holiday → fall back to prior trading day."""
    bars = _make_bars([
        (date(2026, 5, 13), 100.0),   # last trading day before earnings
        # earnings date 2026-05-14 (holiday, no bar)
        (date(2026, 5, 15), 97.0),    # → -3% from prior close
    ])
    assert EarningsHistory._next_day_move(bars, date(2026, 5, 14)) == pytest.approx(-3.0)


def test_next_day_move_returns_none_when_no_next_day():
    bars = _make_bars([(date(2026, 5, 14), 100.0)])
    assert EarningsHistory._next_day_move(bars, date(2026, 5, 14)) is None


def test_next_day_move_returns_none_when_date_before_all_bars():
    bars = _make_bars([(date(2026, 5, 14), 100.0), (date(2026, 5, 15), 101.0)])
    assert EarningsHistory._next_day_move(bars, date(2025, 1, 1)) is None


# ── COMPUTE / GET_REACTIONS ───────────────────────────

def test_get_reactions_aggregates_stats(iso_dirs):
    past_dates = [date(2026, 2, 1), date(2025, 11, 1)]
    bars = _make_bars([
        (date(2025, 10, 31), 100.0),
        (date(2025, 11,  3), 105.0),   # +5% after Nov earnings
        (date(2026,  1, 30), 200.0),
        (date(2026,  2,  2), 196.0),   # -2% after Feb earnings
    ])
    h = EarningsHistory(
        date_fetcher = lambda t: past_dates,
        bars_fetcher = lambda t, days: bars,
    )
    r = h.get_reactions("AAPL")
    assert r["ticker"] == "AAPL"
    assert r["n"] == 2
    # mean abs(|5|, |-2|) = 3.5 → boundary lands in 'volatile' class
    assert r["mean_abs_move_pct"] == pytest.approx(3.5)
    assert r["gap_class"] == "volatile"
    moves = sorted(x["move_pct"] for x in r["reactions"])
    assert moves == pytest.approx([-2.0, 5.0])


def test_get_reactions_returns_none_when_no_dates(iso_dirs):
    h = EarningsHistory(date_fetcher=lambda t: [], bars_fetcher=lambda t, d: None)
    assert h.get_reactions("ZZZZ") is None


def test_get_reactions_returns_none_when_no_bars(iso_dirs):
    h = EarningsHistory(
        date_fetcher=lambda t: [date(2026, 2, 1)],
        bars_fetcher=lambda t, d: None,
    )
    assert h.get_reactions("AAPL") is None


def test_get_reactions_skips_dates_with_no_next_bar(iso_dirs):
    past = [date(2026, 2, 1), date(2026, 5, 14)]   # 5/14 has no next bar
    bars = _make_bars([
        (date(2026, 1, 30), 100.0),
        (date(2026, 2,  2), 103.0),   # +3% Feb
        (date(2026, 5, 14), 200.0),   # no bar after
    ])
    h = EarningsHistory(date_fetcher=lambda t: past, bars_fetcher=lambda t, d: bars)
    r = h.get_reactions("AAPL")
    assert r["n"] == 1
    assert r["reactions"][0]["move_pct"] == pytest.approx(3.0)


# ── CACHE ─────────────────────────────────────────────

def test_get_reactions_uses_cache_on_second_call(iso_dirs):
    calls = {"count": 0}
    def date_fetcher(t):
        calls["count"] += 1
        return [date(2026, 2, 1)]
    bars = _make_bars([
        (date(2026, 1, 30), 100.0),
        (date(2026, 2,  2), 101.0),
    ])
    h = EarningsHistory(date_fetcher=date_fetcher, bars_fetcher=lambda t, d: bars)
    r1 = h.get_reactions("AAPL")
    r2 = h.get_reactions("AAPL")
    assert r1 == r2
    assert calls["count"] == 1   # second call served from cache


def test_refresh_true_skips_cache(iso_dirs):
    calls = {"count": 0}
    def date_fetcher(t):
        calls["count"] += 1
        return [date(2026, 2, 1)]
    bars = _make_bars([
        (date(2026, 1, 30), 100.0),
        (date(2026, 2,  2), 101.0),
    ])
    h = EarningsHistory(date_fetcher=date_fetcher, bars_fetcher=lambda t, d: bars)
    h.get_reactions("AAPL")
    h.get_reactions("AAPL", refresh=True)
    assert calls["count"] == 2


def test_cache_persists_to_disk(iso_dirs):
    bars = _make_bars([
        (date(2026, 1, 30), 100.0),
        (date(2026, 2,  2), 102.0),
    ])
    h = EarningsHistory(
        date_fetcher=lambda t: [date(2026, 2, 1)],
        bars_fetcher=lambda t, d: bars,
    )
    h.get_reactions("AAPL")
    cache_file = os.path.join(str(iso_dirs), "earnings_history.json")
    assert os.path.exists(cache_file)
    raw = json.loads(open(cache_file).read())
    assert "AAPL" in raw
    assert raw["AAPL"]["data"]["ticker"] == "AAPL"


# ── ANNOTATE ──────────────────────────────────────────

def test_annotate_upcoming_merges_reaction_stats(iso_dirs):
    bars = _make_bars([
        (date(2026, 1, 30), 100.0),
        (date(2026, 2,  2), 105.0),   # +5%
    ])
    h = EarningsHistory(
        date_fetcher = lambda t: [date(2026, 2, 1)] if t == "AAPL" else [],
        bars_fetcher = lambda t, d: bars if t == "AAPL" else None,
    )
    upcoming = [
        {"ticker": "AAPL", "earnings_date": "2026-05-20", "days_away": 3},
        {"ticker": "UNKN", "earnings_date": "2026-05-21", "days_away": 4},
    ]
    out = h.annotate_upcoming(upcoming)
    aapl = next(x for x in out if x["ticker"] == "AAPL")
    assert aapl["mean_abs_move_pct"] == pytest.approx(5.0)
    assert aapl["gap_class"] == "volatile"
    unkn = next(x for x in out if x["ticker"] == "UNKN")
    assert "gap_class" not in unkn  # passes through unchanged
