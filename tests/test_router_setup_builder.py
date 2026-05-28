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
