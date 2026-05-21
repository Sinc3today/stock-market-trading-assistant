"""
tests/test_intraday_data.py -- cached, paginated intraday bars.

Fetch + cache roundtrip is unit-tested with a fake client into a tmp cache
dir; the live Polygon pull is marked integration.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import data.intraday_data as idata


class _Bar:
    def __init__(self, ts, c):
        self.timestamp = ts
        self.open = self.high = self.low = self.close = c
        self.volume = 1000


class _FakeClient:
    def __init__(self, bars): self._bars = bars; self.calls = 0
    def list_aggs(self, *a, **k):
        self.calls += 1
        return iter(self._bars)


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(idata, "_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def test_fetch_builds_dataframe(tmp_cache):
    bars = [_Bar(1704207600000, 470.0), _Bar(1704207900000, 471.0)]
    df = idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 2), date(2024, 1, 3),
                                  client=_FakeClient(bars))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2 and df["close"].iloc[-1] == 471.0


def test_cache_roundtrip_avoids_second_fetch(tmp_cache):
    bars = [_Bar(1704207600000, 470.0)]
    fake = _FakeClient(bars)
    a = idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 2), date(2024, 1, 3), client=fake)
    # Second call should hit the parquet cache, not the client.
    b = idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 2), date(2024, 1, 3), client=fake)
    assert fake.calls == 1
    assert len(a) == len(b) == 1


def test_use_cache_false_refetches(tmp_cache):
    fake = _FakeClient([_Bar(1704207600000, 470.0)])
    idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 2), date(2024, 1, 3), client=fake)
    idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 2), date(2024, 1, 3),
                             client=fake, use_cache=False)
    assert fake.calls == 2


def test_empty_on_no_bars(tmp_cache):
    df = idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 2), date(2024, 1, 3),
                                  client=_FakeClient([]))
    assert df.empty


@pytest.mark.integration
def test_live_intraday_spans_full_range():
    """list_aggs must paginate the whole window (the get_aggs bug returned
    only the oldest slice)."""
    df = idata.get_stock_intraday("SPY", 5, "minute", date(2024, 1, 1), date(2024, 3, 31),
                                  use_cache=False)
    assert not df.empty
    # Should reach into March, not stop in January.
    assert df.index.max().month >= 3
