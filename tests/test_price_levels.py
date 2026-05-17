"""
tests/test_price_levels.py -- swing-level + MA helpers for the /levels page.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.price_levels import (
    recent_swing_levels,
    moving_average_levels,
    distance_pct,
)


def _make_bars(closes: list[float]) -> pd.DataFrame:
    """Synthesize a daily-bar DataFrame from a list of closes.
    High/low are close±1 to give the swing logic something to chew on."""
    start = date(2026, 1, 5)
    rows = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        rows.append({"timestamp": d, "open": c, "high": c + 1, "low": c - 1,
                     "close": c, "volume": 1_000_000})
    df = pd.DataFrame(rows).set_index("timestamp").sort_index()
    return df


# ── moving_average_levels ─────────────────────────────

def test_moving_average_levels_uses_last_n_closes():
    df = _make_bars(list(range(1, 251)))   # closes 1..250
    mas = moving_average_levels(df)
    # MA20 = mean of closes 231..250
    assert mas["ma20"]  == pytest.approx(sum(range(231, 251)) / 20)
    assert mas["ma50"]  == pytest.approx(sum(range(201, 251)) / 50)
    assert mas["ma200"] == pytest.approx(sum(range( 51, 251)) / 200)
    assert mas["close"] == 250.0


def test_moving_average_levels_returns_none_when_short():
    df = _make_bars([100, 101, 102])
    mas = moving_average_levels(df)
    assert mas["close"] == 102.0
    assert mas["ma20"]  is None
    assert mas["ma50"]  is None
    assert mas["ma200"] is None


def test_moving_average_levels_empty_input():
    mas = moving_average_levels(pd.DataFrame())
    assert mas == {"ma20": None, "ma50": None, "ma200": None, "close": None}


# ── distance_pct ──────────────────────────────────────

def test_distance_pct_positive():
    assert distance_pct(105.0, 100.0) == pytest.approx(5.0)


def test_distance_pct_negative():
    assert distance_pct(95.0, 100.0) == pytest.approx(-5.0)


def test_distance_pct_handles_none_and_zero():
    assert distance_pct(None, 100.0) is None
    assert distance_pct(100.0, None) is None
    assert distance_pct(100.0, 0)    is None


# ── recent_swing_levels ───────────────────────────────

def test_swing_levels_returns_lookback_high_low():
    # 60 bars, deliberate spike at index 50
    closes = [100.0] * 60
    closes[50] = 120.0
    df = _make_bars(closes)
    levels = recent_swing_levels(df, lookback=30)
    assert levels["high_N"] == pytest.approx(121.0)   # close+1
    # All closes are 100 except spike → low_N = 99 (close - 1)
    assert levels["low_N"]  == pytest.approx(99.0)
    assert levels["lookback"] == 30


def test_swing_levels_finds_local_pivot_highs():
    """Build a clean wave: gradually up to a peak, back down, up again."""
    closes = [100, 101, 102, 103, 104, 105, 110, 105, 104, 103,
              102, 101, 100, 101, 102, 103, 104, 105, 108, 105, 104]
    df = _make_bars(closes)
    levels = recent_swing_levels(df, lookback=50)
    # Pivot windows look at high which = close+1 here
    pivot_highs = [(d, p) for d, p in levels["swing_highs"]]
    # Should detect the 110 spike (index 6 → high 111) at minimum
    assert any(p == pytest.approx(111.0) for _, p in pivot_highs)


def test_swing_levels_empty_input():
    levels = recent_swing_levels(pd.DataFrame(), lookback=20)
    assert levels["high_N"]      is None
    assert levels["swing_highs"] == []


def test_swing_levels_handles_missing_high_low_columns():
    df = pd.DataFrame({"close": [100, 101, 102]})
    levels = recent_swing_levels(df, lookback=10)
    assert levels["high_N"]      is None
    assert levels["swing_highs"] == []


def test_swing_pivots_outside_lookback_dropped():
    """A pivot from 200 bars ago should NOT appear in a lookback=20 result."""
    closes = [100.0] * 60
    closes[5] = 150.0   # ancient pivot — outside last 20 bars
    df = _make_bars(closes)
    levels = recent_swing_levels(df, lookback=20)
    # The ancient spike at index 5 is before the lookback window
    assert all(p != pytest.approx(151.0) for _, p in levels["swing_highs"])
