"""
tests/test_options_walls.py -- heavy-strike walls + max-pain math.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.options_walls import compute_walls, max_pain, load_walls


# ── compute_walls ─────────────────────────────────────

def test_compute_walls_returns_top_n_by_oi():
    calls = [
        {"strike": 510, "open_interest": 5_000},
        {"strike": 515, "open_interest": 20_000},
        {"strike": 520, "open_interest": 12_000},
        {"strike": 525, "open_interest": 1_000},
    ]
    puts = [
        {"strike": 495, "open_interest": 18_000},
        {"strike": 490, "open_interest": 25_000},
        {"strike": 485, "open_interest": 3_000},
    ]
    out = compute_walls(calls, puts, spot=505.0, top_n=2)
    assert [w["strike"] for w in out["call_walls"]] == [515.0, 520.0]
    assert [w["strike"] for w in out["put_walls"]]  == [490.0, 495.0]
    # Distance from spot is in %
    assert out["call_walls"][0]["distance_pct"] == pytest.approx((515-505)/505*100, abs=0.01)
    assert out["put_walls"][0]["distance_pct"]  == pytest.approx((490-505)/505*100, abs=0.01)


def test_compute_walls_handles_missing_oi():
    calls = [{"strike": 510, "open_interest": None},
             {"strike": 515, "open_interest": 1_000}]
    out = compute_walls(calls, [], spot=505.0, top_n=2)
    # Contract with None OI excluded
    assert [w["strike"] for w in out["call_walls"]] == [515.0]


def test_compute_walls_empty_chain():
    out = compute_walls([], [], spot=None, top_n=3)
    assert out["call_walls"] == []
    assert out["put_walls"]  == []
    assert out["max_pain"]   is None


# ── max_pain ──────────────────────────────────────────

def test_max_pain_centers_on_heavy_short_strike():
    """Heavy call OI at 520 + heavy put OI at 500 → max-pain price should
    be the strike that minimizes total intrinsic. Expect somewhere in
    the 500-520 corridor."""
    calls = [
        {"strike": 500, "open_interest":     500},
        {"strike": 510, "open_interest":   1_000},
        {"strike": 520, "open_interest":  20_000},
        {"strike": 530, "open_interest":   2_000},
    ]
    puts = [
        {"strike": 480, "open_interest":   2_000},
        {"strike": 490, "open_interest":   1_000},
        {"strike": 500, "open_interest":  20_000},
        {"strike": 510, "open_interest":     500},
    ]
    mp = max_pain(calls, puts)
    assert mp is not None
    assert 500.0 <= mp <= 520.0


def test_max_pain_handles_empty_chains():
    assert max_pain([], []) is None


def test_max_pain_pure_call_chain_pushes_price_low():
    """All OI is in calls — minimizing pain = price at or below the lowest
    call strike (no calls are ITM)."""
    calls = [
        {"strike": 500, "open_interest": 10_000},
        {"strike": 510, "open_interest":  5_000},
    ]
    mp = max_pain(calls, [])
    assert mp == 500.0


# ── load_walls (integration with OptionsChain stub) ──

class _StubChain:
    def __init__(self, calls, puts):
        self._calls = calls
        self._puts  = puts
        self.calls_args = None
        self.puts_args  = None
    def get_chain(self, ticker, ctype, min_exp, max_exp,
                  strike_min=None, strike_max=None, limit=200):
        if ctype == "call":
            self.calls_args = (ticker, min_exp, max_exp, strike_min, strike_max)
            return self._calls
        self.puts_args = (ticker, min_exp, max_exp, strike_min, strike_max)
        return self._puts


def test_load_walls_uses_injected_chain_and_returns_expiration():
    calls = [{"strike": 510, "open_interest": 5_000, "expiration": "2026-06-20"},
             {"strike": 515, "open_interest": 8_000, "expiration": "2026-06-20"}]
    puts  = [{"strike": 495, "open_interest": 10_000, "expiration": "2026-06-20"}]
    stub  = _StubChain(calls, puts)
    out   = load_walls("SPY", spot=505.0, options_chain=stub, top_n=2)
    assert out["spot"] == 505.0
    assert out["expiration"] == "2026-06-20"
    assert out["call_walls"][0]["strike"] == 515.0
    assert out["put_walls"][0]["strike"]  == 495.0
    # The chain fetcher was asked for both sides
    assert stub.calls_args is not None
    assert stub.puts_args  is not None


def test_load_walls_returns_empty_when_chain_empty():
    out = load_walls("SPY", spot=505.0, options_chain=_StubChain([], []))
    assert out["call_walls"] == []
    assert out["put_walls"]  == []
    assert out["max_pain"]   is None


def test_load_walls_returns_empty_on_chain_exception():
    class Boom:
        def get_chain(self, *a, **kw):
            raise RuntimeError("polygon down")
    out = load_walls("SPY", spot=505.0, options_chain=Boom())
    assert out["call_walls"] == []
    assert out["max_pain"]   is None
    assert out["spot"]       == 505.0
