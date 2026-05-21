"""
tests/test_options_history.py -- historical option aggregates layer.

Ticker construction + DataFrame shaping are unit-tested with a fake client;
the live Polygon fetch is marked integration.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.options_history import option_ticker, OptionsHistory


# ── Ticker construction (pure) ─────────────────────────

def test_option_ticker_format():
    assert option_ticker("SPY", date(2024, 8, 16), "C", 550) == "O:SPY240816C00550000"
    assert option_ticker("SPY", date(2026, 1, 2), "P", 612.5) == "O:SPY260102P00612500"


def test_option_ticker_pads_strike_and_lowercases_underlying():
    assert option_ticker("spy", date(2024, 8, 16), "c", 5) == "O:SPY240816C00005000"


def test_option_ticker_rejects_bad_cp():
    with pytest.raises(ValueError):
        option_ticker("SPY", date(2024, 8, 16), "X", 550)


# ── get_aggs shaping (fake client) ─────────────────────

class _Bar:
    def __init__(self, ts, o, h, l, c, v):
        self.timestamp, self.open, self.high, self.low, self.close, self.volume = ts, o, h, l, c, v


class _FakeClient:
    def __init__(self, bars): self._bars = bars; self.calls = 0
    def list_aggs(self, *a, **k):
        self.calls += 1
        return iter(self._bars)


class _BoomClient:
    def list_aggs(self, *a, **k): raise RuntimeError("api down")


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    """Redirect the parquet cache to a tmp dir so tests stay hermetic."""
    import data.options_history as oh_mod
    monkeypatch.setattr(oh_mod, "_CACHE_DIR", str(tmp_path / "opt_cache"))


def test_get_aggs_builds_dataframe():
    bars = [_Bar(1721016000000, 18.0, 19.0, 17.5, 17.48, 1017),
            _Bar(1721102400000, 17.5, 18.2, 16.9, 17.10, 800)]
    oh = OptionsHistory(client=_FakeClient(bars))
    df = oh.get_aggs("O:SPY240816C00550000", 1, "day", date(2024, 7, 15), date(2024, 8, 16))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["close"].iloc[0] == 17.48


def test_get_aggs_empty_on_no_bars():
    oh = OptionsHistory(client=_FakeClient([]))
    assert oh.get_aggs("O:X", 1, "day", date(2024, 1, 1), date(2024, 1, 2)).empty


def test_get_aggs_empty_on_exception():
    oh = OptionsHistory(client=_BoomClient())
    assert oh.get_aggs("O:X", 1, "day", date(2024, 1, 1), date(2024, 1, 2)).empty


def test_cache_roundtrip_skips_refetch():
    bars = [_Bar(1721016000000, 18.0, 19.0, 17.5, 17.48, 1017)]
    fake = _FakeClient(bars)
    oh = OptionsHistory(client=fake)
    a = oh.get_aggs("O:SPY240816C00550000", 1, "day", date(2024, 7, 15), date(2024, 8, 16))
    b = oh.get_aggs("O:SPY240816C00550000", 1, "day", date(2024, 7, 15), date(2024, 8, 16))
    assert fake.calls == 1            # second call served from parquet cache
    assert len(a) == len(b) == 1


def test_empty_results_are_cached_too():
    """An illiquid strike with no bars must not re-fetch every run."""
    fake = _FakeClient([])
    oh = OptionsHistory(client=fake)
    oh.get_aggs("O:SPY240816C00999000", 5, "minute", date(2024, 8, 16), date(2024, 8, 16))
    oh.get_aggs("O:SPY240816C00999000", 5, "minute", date(2024, 8, 16), date(2024, 8, 16))
    assert fake.calls == 1            # empty result cached, no second fetch


def test_leg_close_returns_last_close():
    bars = [_Bar(1721016000000, 18.0, 19.0, 17.5, 17.48, 1017)]
    oh = OptionsHistory(client=_FakeClient(bars))
    px = oh.leg_close("SPY", date(2024, 8, 16), "C", 550, date(2024, 7, 15))
    assert px == 17.48


def test_leg_close_none_when_no_data():
    oh = OptionsHistory(client=_FakeClient([]))
    assert oh.leg_close("SPY", date(2024, 8, 16), "C", 550, date(2024, 7, 15)) is None


# ── Live fetch (paid tier) ─────────────────────────────

@pytest.mark.integration
def test_live_option_aggs_fetch():
    oh = OptionsHistory()
    df = oh.get_aggs("O:SPY240816C00550000", 1, "day", date(2024, 7, 15), date(2024, 8, 16))
    assert not df.empty
    assert "close" in df.columns and (df["close"] > 0).all()
