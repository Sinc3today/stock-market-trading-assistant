"""Tests for backtests/router_setup_builder.py."""

import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.router_setup_builder import load_daily_history


def test_load_daily_history_returns_dataframe_through_date():
    df = load_daily_history(date(2024, 6, 14))
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    # spy_history.csv has columns: open, high, low, close, volume (date as index)
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns, f"missing column: {col}"
    # The last row must be <= the cutoff (no lookahead)
    assert df.index.max() <= pd.Timestamp("2024-06-14")


def test_load_daily_history_excludes_target_date():
    """The cutoff date should be exclusive — the LAST completed daily bar is yesterday."""
    df = load_daily_history(date(2024, 6, 14))
    assert pd.Timestamp("2024-06-14") not in df.index


def test_load_daily_history_short_window_raises():
    """Caller requesting a date before spy_history.csv starts gets a clear error."""
    with pytest.raises(ValueError, match="insufficient daily history"):
        load_daily_history(date(2020, 1, 1))


from unittest.mock import patch
from backtests.router_setup_builder import load_intraday_window


def _make_5min_bars(target_date, utc_hhmm: str = "13:30:00"):
    """Synthetic 5-min bars from utc_hhmm to +6.5h, tz-naive (matching
    data.intraday_data.get_stock_intraday's real return shape).
    Default utc_hhmm='13:30:00' covers EDT (summer) 09:30 ET; pass
    '14:30:00' for EST (winter) 09:30 ET.
    """
    start = pd.Timestamp(f"{target_date.isoformat()} {utc_hhmm}")   # tz-naive
    idx = pd.date_range(start, periods=78, freq="5min")
    return pd.DataFrame({
        "open":   [500.0] * 78,
        "high":   [501.0] * 78,
        "low":    [499.0] * 78,
        "close":  [500.5] * 78,
        "volume": [1000]  * 78,
    }, index=idx)


def test_load_intraday_window_slices_9_30_to_9_45_ET():
    """Should return only the first 15 minutes (3 bars at 5-min)."""
    target = date(2024, 6, 14)
    with patch("backtests.router_setup_builder.get_stock_intraday",
               return_value=_make_5min_bars(target)):
        df = load_intraday_window(target)
    assert not df.empty
    # 09:30, 09:35, 09:40 — three 5-min bars before 09:45 cutoff
    assert len(df) == 3
    # Index should be tz-aware UTC (downstream tz handling lives in the engine)
    assert df.index.tz is not None


def test_load_intraday_window_returns_empty_when_no_data():
    target = date(2024, 6, 14)
    with patch("backtests.router_setup_builder.get_stock_intraday",
               return_value=pd.DataFrame()):
        df = load_intraday_window(target)
    assert df.empty


def test_load_intraday_window_handles_EST_session():
    """Winter date (EST) — same 3-bar slice. Validates tz_convert handles DST.
    In EST, 09:30 ET = 14:30 UTC (vs 13:30 UTC in EDT)."""
    target = date(2024, 1, 16)
    with patch("backtests.router_setup_builder.get_stock_intraday",
               return_value=_make_5min_bars(target, utc_hhmm="14:30:00")):
        df = load_intraday_window(target)
    assert len(df) == 3
    assert df.index.tz is not None
