"""
tests/test_sector_breadth.py -- SectorBreadth with mocked Polygon.

Builds synthetic OHLC frames so we can drive the dispersion / regime
signal logic without any network calls.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.sector_breadth import SectorBreadth, SECTORS


class FakePolygon:
    """
    Returns a 30-bar daily DataFrame for each ticker.

    `returns_pct` keys = ticker, values = pct change over the window.
    Tickers not in the dict get a flat series (0% return).
    Tickers in `unavailable` return None to simulate fetch failure.
    """
    def __init__(self, returns_pct: dict, unavailable: set | None = None):
        self.returns_pct = returns_pct
        self.unavailable = unavailable or set()

    def get_bars(self, ticker, **kwargs):
        if ticker in self.unavailable:
            return None
        # Return exactly (limit) bars so the implementation's window logic
        # (`start_idx = len - days - 1`) yields the FULL requested return.
        n = kwargs.get("limit") or 25
        start = 100.0
        end   = start * (1 + self.returns_pct.get(ticker, 0.0) / 100)
        # First bar = start, last bar = end, linear in between.
        prices = [start + (end - start) * (i / (n - 1)) for i in range(n)]
        # _period_return wants close[start_idx]→close[-1] over `days` bars;
        # start_idx = n - days - 1. Set price at that index = `start` so we
        # get the full return, regardless of the buffer at the front.
        # Default impl: start_idx = n - days - 1. We want prices[start_idx]=start.
        # Easier: just make the whole series the same return by setting prices
        # so prices[start_idx] == start. Since we built linear start→end with
        # start at index 0, anchor start to start_idx by shifting:
        days = kwargs.get("limit", 25) - 5   # match impl's "days+5" buffer
        if days > 0 and days < n:
            start_idx = n - days - 1
            # Rebuild: keep prices[start_idx]=start and prices[-1]=end
            prices = (
                [start] * (start_idx + 1)
                + [start + (end - start) * ((i + 1) / (n - start_idx - 1))
                   for i in range(n - start_idx - 1)]
            )
        return pd.DataFrame({
            "open": prices, "high": prices, "low": prices,
            "close": prices, "volume": [1_000_000] * n,
        })


# ── compute_relative_strength ──────────────────────────────────────

def test_relative_strength_subtracts_spy_return():
    """Sector +5%, SPY +2% -> RS = +3 percentage points."""
    rs = SectorBreadth(FakePolygon({
        "SPY": 2.0,
        "XLK": 5.0,
        "XLF": 0.0,
    })).compute_relative_strength(days=20)
    assert rs["XLK"] == pytest.approx(3.0,  abs=0.01)
    assert rs["XLF"] == pytest.approx(-2.0, abs=0.01)


def test_relative_strength_returns_none_when_spy_unavailable():
    sb = SectorBreadth(FakePolygon({"XLK": 5.0}, unavailable={"SPY"}))
    assert sb.compute_relative_strength(days=20) is None


def test_relative_strength_skips_missing_sectors():
    sb = SectorBreadth(FakePolygon(
        {"SPY": 2.0, "XLK": 5.0, "XLF": 1.0},
        unavailable={"XLE"},
    ))
    rs = sb.compute_relative_strength(days=20)
    assert "XLE" not in rs
    assert "XLK" in rs


# ── dispersion / leaders-laggards ──────────────────────────────────

def test_dispersion_high_when_sectors_diverge():
    """Half sectors +10%, half -10% -> high dispersion."""
    returns = {"SPY": 0.0}
    sectors = list(SECTORS.keys())
    for i, s in enumerate(sectors):
        returns[s] = 10.0 if i % 2 == 0 else -10.0
    sb = SectorBreadth(FakePolygon(returns))
    d  = sb.dispersion_score()
    assert d > 5.0


def test_dispersion_low_when_sectors_aligned():
    """All sectors close to SPY -> tight dispersion."""
    returns = {"SPY": 2.0}
    for s in SECTORS:
        returns[s] = 2.0 + (hash(s) % 3 - 1) * 0.05   # +/- 0.05pp around SPY
    sb = SectorBreadth(FakePolygon(returns))
    d  = sb.dispersion_score()
    assert d is not None and d < 1.0


def test_leaders_and_laggards_order():
    returns = {"SPY": 0.0,
               "XLK": 5.0, "XLF": 3.0, "XLE": -2.0,
               "XLV": 1.0, "XLY": -4.0, "XLP": 0.0,
               "XLI": 2.0, "XLB": -1.0, "XLU": -3.0, "XLRE": 0.5}
    leaders, laggards = SectorBreadth(FakePolygon(returns)).leaders_and_laggards(n=3)
    assert leaders[0][0]  == "XLK"
    assert laggards[0][0] == "XLY"
    assert len(leaders)   == 3
    assert len(laggards)  == 3


# ── regime_signal ──────────────────────────────────────────────────

def test_regime_signal_trending_aligned_up():
    """All 10 sectors mildly up vs SPY -> aligned uptrend."""
    returns = {"SPY": 0.0}
    for s in SECTORS:
        returns[s] = 0.3   # small uniform positive RS
    assert SectorBreadth(FakePolygon(returns)).regime_signal() == "trending_aligned"


def test_regime_signal_dispersed_on_wide_spread():
    returns = {"SPY": 0.0}
    sectors = list(SECTORS.keys())
    for i, s in enumerate(sectors):
        returns[s] = 8.0 if i % 2 == 0 else -8.0
    assert SectorBreadth(FakePolygon(returns)).regime_signal() == "dispersed"


def test_regime_signal_rotating_when_moderate():
    """Mixed signs, moderate dispersion -> rotating."""
    returns = {"SPY": 0.0,
               "XLK": 2.5, "XLF": 1.2, "XLY": 2.0, "XLI": 1.8, "XLB": 0.5,
               "XLE": -2.5, "XLV": -1.0, "XLP": -1.2, "XLU": -2.2, "XLRE": -0.8}
    sig = SectorBreadth(FakePolygon(returns)).regime_signal()
    assert sig == "rotating"


def test_regime_signal_unknown_when_spy_fails():
    sb = SectorBreadth(FakePolygon({"XLK": 5.0}, unavailable={"SPY"}))
    assert sb.regime_signal() == "unknown"


# ── snapshot ──────────────────────────────────────────────────────

def test_snapshot_full_shape():
    returns = {"SPY": 0.0}
    for s in SECTORS:
        returns[s] = 1.0
    snap = SectorBreadth(FakePolygon(returns)).snapshot(days=20)
    assert set(snap.keys()) == {"leaders", "laggards", "dispersion",
                                 "signal", "rs", "asof", "horizon"}
    assert snap["horizon"] == 20
    assert len(snap["leaders"])  == 3
    assert len(snap["laggards"]) == 3
    assert snap["signal"]    in ("trending_aligned", "rotating", "dispersed")


def test_snapshot_degraded_when_spy_unavailable():
    snap = SectorBreadth(FakePolygon({}, unavailable={"SPY"})).snapshot()
    assert snap["leaders"]    == []
    assert snap["dispersion"] is None
    assert snap["signal"]     == "unknown"
