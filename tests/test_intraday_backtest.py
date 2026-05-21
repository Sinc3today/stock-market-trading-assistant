"""
tests/test_intraday_backtest.py -- 0DTE structure decision + leg construction.

Pure logic is unit-tested; the real-priced day simulator + runner pull live
option data and are marked integration.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests import intraday_backtest as ib


# ── decide_structure (the regime-split + high-vol reframe) ─────────────

def test_range_day_gets_condor():
    assert ib.decide_structure("choppy_low_vol", "neutral", False, False) == ("iron_condor", "neutral")


def test_trend_days_get_directional_debit():
    assert ib.decide_structure("trending_up_calm", "bullish", False, False) == ("bull_debit", "bullish")
    assert ib.decide_structure("trending_down_calm", "bearish", False, False) == ("bear_debit", "bearish")


def test_high_vol_day_engages_directionally_not_skip():
    """The reframe: high-vol days route to a directional debit (the swing
    strategy skips them; 0DTE engages the direction)."""
    assert ib.decide_structure("trending_high_vol", "bullish", True, False) == ("bull_debit", "bullish")
    assert ib.decide_structure("trending_high_vol", "bearish", True, False) == ("bear_debit", "bearish")


def test_event_day_is_directional_only_not_condor():
    """Condors die on the release; a debit can ride the move."""
    assert ib.decide_structure("choppy_low_vol", "bullish", False, True) == ("bull_debit", "bullish")
    # No directional read on a high-vol/event day → stand down.
    assert ib.decide_structure("choppy_low_vol", "neutral", False, True) is None


def test_no_clear_regime_skips():
    assert ib.decide_structure("unknown", "neutral", False, False) is None


# ── build_0dte_legs ────────────────────────────────────

def test_condor_has_four_legs_around_spot():
    legs = ib.build_0dte_legs(550.0, "iron_condor")
    assert len(legs) == 4
    puts  = sorted(l["strike"] for l in legs if l["cp"] == "P")
    calls = sorted(l["strike"] for l in legs if l["cp"] == "C")
    assert puts[0] < puts[1] <= 550 <= calls[0] < calls[1]   # wings outside shorts
    assert sum(1 for l in legs if l["action"] == "SELL") == 2


def test_bull_debit_buys_atm_sells_otm_call():
    legs = ib.build_0dte_legs(550.0, "bull_debit")
    assert len(legs) == 2
    buy  = next(l for l in legs if l["action"] == "BUY")
    sell = next(l for l in legs if l["action"] == "SELL")
    assert buy["cp"] == sell["cp"] == "C"
    assert sell["strike"] > buy["strike"]      # short is further OTM


def test_bear_debit_buys_atm_sells_otm_put():
    legs = ib.build_0dte_legs(550.0, "bear_debit")
    sell = next(l for l in legs if l["action"] == "SELL")
    buy  = next(l for l in legs if l["action"] == "BUY")
    assert buy["cp"] == sell["cp"] == "P"
    assert sell["strike"] < buy["strike"]


def test_strikes_rounded_to_dollar():
    legs = ib.build_0dte_legs(550.37, "bull_debit")
    assert all(float(l["strike"]).is_integer() for l in legs)


def test_is_credit_structure():
    assert ib.is_credit_structure("iron_condor")
    assert not ib.is_credit_structure("bull_debit")


# ── confirm_entry (the blend: opening-range + VWAP) ────

def test_condor_confirms_inside_range_near_vwap():
    # price inside [or_low, or_high] and right at VWAP → confirmed
    assert ib.confirm_entry("iron_condor", or_high=552, or_low=548, vwap=550.0, price=550.0)


def test_condor_rejected_when_broken_out_or_far_from_vwap():
    assert not ib.confirm_entry("iron_condor", 552, 548, vwap=550.0, price=553.0)   # broke range
    assert not ib.confirm_entry("iron_condor", 552, 548, vwap=550.0, price=551.5)   # far from VWAP


def test_bull_debit_confirms_only_above_range_and_vwap():
    assert ib.confirm_entry("bull_debit", or_high=552, or_low=548, vwap=550.5, price=553.0)
    assert not ib.confirm_entry("bull_debit", 552, 548, vwap=550.5, price=551.0)   # not above OR high
    assert not ib.confirm_entry("bull_debit", 552, 548, vwap=553.5, price=553.0)   # below VWAP


def test_bear_debit_confirms_only_below_range_and_vwap():
    assert ib.confirm_entry("bear_debit", or_high=552, or_low=548, vwap=549.5, price=547.0)
    assert not ib.confirm_entry("bear_debit", 552, 548, vwap=549.5, price=549.0)   # not below OR low


def test_vwap_weights_by_volume():
    import pandas as pd
    bars = pd.DataFrame({
        "high":   [101, 201], "low": [99, 199], "close": [100, 200],
        "volume": [100, 900],
    })
    # Heavily weighted to the 200 bar → VWAP near 190
    assert 180 < ib._session_vwap(bars) < 200


# ── Real-priced simulation (live data) ─────────────────

@pytest.mark.integration
def test_simulate_0dte_day_live():
    from data.intraday_data import get_stock_intraday
    from data.options_history import OptionsHistory
    d = date(2024, 8, 14)
    spy = get_stock_intraday("SPY", 5, "minute", d, d, use_cache=True)
    # require_confirmation=False to deterministically exercise the pricing path
    # (confirmation depends on that day's tape).
    r = ib.simulate_0dte_day(d, "iron_condor", spy, OptionsHistory(),
                             require_confirmation=False)
    assert r is not None
    assert "pnl_dollars" in r and r["structure"] == "iron_condor"
    assert r["exit_reason"] in ("target", "stop", "eod")
