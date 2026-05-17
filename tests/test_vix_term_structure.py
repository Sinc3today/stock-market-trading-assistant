"""
tests/test_vix_term_structure.py -- VIXTermStructure with mocked CBOE CSVs.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import data.vix_term_structure as vts_mod
from data.vix_term_structure import VIXTermStructure


@pytest.fixture(autouse=True)
def clear_cache():
    """Each test starts with a clean module-level cache."""
    vts_mod._cache.update({"current": None, "fetched_at": None})


def _fake_fetch(values: dict):
    """Replacement for _fetch_latest_close that returns canned values per URL."""
    def fn(url: str):
        for sym, v in values.items():
            if f"/{sym}_History.csv" in url:
                return v
        return None
    return fn


def test_fetch_current_returns_dict(monkeypatch):
    monkeypatch.setattr(
        VIXTermStructure, "_fetch_latest_close",
        staticmethod(_fake_fetch({"VIX9D": 13.2, "VIX": 14.1, "VIX3M": 15.0, "VIX6M": 16.0})),
    )
    out = VIXTermStructure().fetch_current()
    assert out == {"VIX9D": 13.2, "VIX": 14.1, "VIX3M": 15.0, "VIX6M": 16.0}


def test_fetch_current_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(VIXTermStructure, "_fetch_latest_close", staticmethod(lambda url: None))
    assert VIXTermStructure().fetch_current() is None


def test_fetch_current_returns_none_when_core_symbols_missing(monkeypatch):
    """Only VIX9D / VIX6M available — can't compute ratio, must return None."""
    monkeypatch.setattr(
        VIXTermStructure, "_fetch_latest_close",
        staticmethod(_fake_fetch({"VIX9D": 13.0, "VIX6M": 16.0})),
    )
    assert VIXTermStructure().fetch_current() is None


def test_contango_ratio_calm():
    """VIX 14, VIX3M 15 -> ratio ~0.93, calm."""
    r = VIXTermStructure().contango_ratio({"VIX": 14.0, "VIX3M": 15.0})
    assert r == pytest.approx(0.9333, abs=1e-3)


def test_contango_ratio_backwardation():
    """VIX 22, VIX3M 18 -> ratio ~1.22, extreme stress."""
    r = VIXTermStructure().contango_ratio({"VIX": 22.0, "VIX3M": 18.0})
    assert r == pytest.approx(1.2222, abs=1e-3)


def test_contango_ratio_handles_missing_data():
    assert VIXTermStructure().contango_ratio({"VIX": 14.0}) is None
    assert VIXTermStructure().contango_ratio(None) is None or isinstance(
        VIXTermStructure().contango_ratio(None), float
    )


def test_contango_ratio_handles_zero_vix3m():
    assert VIXTermStructure().contango_ratio({"VIX": 14.0, "VIX3M": 0.0}) is None


def test_is_backwardation_true():
    assert VIXTermStructure().is_backwardation({"VIX": 20.0, "VIX3M": 18.0})


def test_is_backwardation_false():
    assert not VIXTermStructure().is_backwardation({"VIX": 14.0, "VIX3M": 16.0})


@pytest.mark.parametrize("vix,vix3m,expected_flag", [
    (12.0, 16.0, "calm"),           # ratio 0.75
    (14.0, 14.0, "calm"),           # ratio 1.00 (boundary)
    (15.5, 14.5, "cautious"),       # ratio ~1.07
    (16.0, 14.0, "stress"),         # ratio ~1.14
    (20.0, 16.0, "extreme_stress"), # ratio 1.25
])
def test_regime_flag(vix, vix3m, expected_flag):
    assert VIXTermStructure().regime_flag({"VIX": vix, "VIX3M": vix3m}) == expected_flag


def test_regime_flag_unknown_when_data_missing(monkeypatch):
    monkeypatch.setattr(VIXTermStructure, "_fetch_latest_close", staticmethod(lambda url: None))
    assert VIXTermStructure().regime_flag() == "unknown"


def test_snapshot_full(monkeypatch):
    monkeypatch.setattr(
        VIXTermStructure, "_fetch_latest_close",
        staticmethod(_fake_fetch({"VIX9D": 13.0, "VIX": 14.0, "VIX3M": 15.0, "VIX6M": 16.0})),
    )
    snap = VIXTermStructure().snapshot()
    assert snap["VIX9D"] == 13.0
    assert snap["VIX"]   == 14.0
    assert snap["VIX3M"] == 15.0
    assert snap["VIX6M"] == 16.0
    assert snap["ratio"] == pytest.approx(0.9333, abs=1e-3)
    assert snap["flag"]  == "calm"
    assert "asof" in snap


def test_snapshot_degraded_when_unavailable(monkeypatch):
    monkeypatch.setattr(VIXTermStructure, "_fetch_latest_close", staticmethod(lambda url: None))
    snap = VIXTermStructure().snapshot()
    assert snap["VIX"]   is None
    assert snap["ratio"] is None
    assert snap["flag"]  == "unknown"


def test_cache_returns_same_object(monkeypatch):
    """Second fetch within TTL returns the cached dict, not a new fetch."""
    call_count = {"n": 0}
    def counting_fetch(url):
        call_count["n"] += 1
        if "VIX_History" in url and "VIX3M" not in url and "VIX6M" not in url and "VIX9D" not in url:
            return 14.0
        if "VIX3M" in url: return 15.0
        if "VIX9D" in url: return 13.0
        if "VIX6M" in url: return 16.0
        return None
    monkeypatch.setattr(VIXTermStructure, "_fetch_latest_close", staticmethod(counting_fetch))

    first  = VIXTermStructure().fetch_current()
    after  = call_count["n"]
    second = VIXTermStructure().fetch_current()

    assert first  == second
    assert call_count["n"] == after   # second call hit cache, no new fetches
