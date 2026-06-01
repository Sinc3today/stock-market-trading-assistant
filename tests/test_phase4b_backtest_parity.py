"""tests/test_phase4b_backtest_parity.py

Parity guard for Task 7: verifies that HistoricalPricer.price() called with
entry_ts and expiry parameters produces the same per-leg marks as the backtest's
inline marks_at() logic, and therefore the same entry_price, max_profit, and
max_loss.

Also tests the build_structure() forwarding of entry_ts and expiry.
"""

import os
import sys
from datetime import date

import pandas as pd
import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.intraday_structure_builder import (
    build_structure,
    HistoricalPricer,
    select_legs,
    _net_premium,
    _risk,
    CONDOR_WING,
    DEBIT_SHORT_OTM,
)
from data.options_history import option_ticker

ET = pytz.timezone("US/Eastern")

# Canonical test day / spot
_DAY = date(2026, 6, 1)
_SPOT = 500.0

# Simulated bar timestamps (ET-aware) to model an intraday session.
# 09:30 bar = first bar of the day (what iloc[0] would give).
# 09:45 bar = entry_ts (opening-range end, what the backtest marks at).
_TS_0930 = pd.Timestamp("2026-06-01 09:30:00", tz="US/Eastern")
_TS_0945 = pd.Timestamp("2026-06-01 09:45:00", tz="US/Eastern")


def _make_history_with_two_bars(prices_at_0945: dict[str, float]) -> object:
    """Return a fake OptionsHistory that returns a 2-bar frame per contract.

    The 09:30 bar always has a DIFFERENT price (1.0 for all contracts) so
    tests that mark at the WRONG bar will fail the numeric assertions.
    The 09:45 bar has the specified price from prices_at_0945.
    """

    class _H:
        def get_aggs(self, contract, multiplier, timespan, from_date, to_date, limit=50000):
            real_px = prices_at_0945.get(contract)
            if real_px is None:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            return pd.DataFrame(
                {"close": [1.0, real_px]},
                index=[_TS_0930, _TS_0945],
            )

    return _H()


# ── Build the expected marks (mirrors the backtest's marks_at logic) ──────────

_IC_PRICES = {
    option_ticker("SPY", _DAY, "P", 497): 1.20,
    option_ticker("SPY", _DAY, "P", 492): 0.40,
    option_ticker("SPY", _DAY, "C", 503): 1.10,
    option_ticker("SPY", _DAY, "C", 508): 0.35,
}
# Net: (1.20+1.10) - (0.40+0.35) = 1.55
_IC_EXPECTED_RAW_ENTRY = 1.55

_BD_DAY = date(2026, 6, 2)  # use a different day so option_ticker keys don't collide
_BD_PRICES = {
    option_ticker("SPY", _BD_DAY, "C", 500): 2.00,
    option_ticker("SPY", _BD_DAY, "C", 503): 0.80,
}
# Net: 2.00 - 0.80 = 1.20
_BD_EXPECTED_RAW_ENTRY = 1.20


# ── HistoricalPricer.price() with entry_ts ────────────────────────────────────

def test_historical_pricer_entry_ts_marks_at_correct_bar():
    """When entry_ts is provided, each leg must be marked at the last bar
    whose index <= entry_ts — matching the backtest's marks_at() logic.

    The 09:30 bar has price 1.0 for ALL legs; the 09:45 bar has the real prices.
    Without entry_ts, marks would use iloc[0] = 09:30 bar (price 1.0).
    With entry_ts=_TS_0945, marks must use the 09:45 bar (real prices).
    """
    history = _make_history_with_two_bars(_IC_PRICES)
    legs = select_legs("iron_condor", spot=_SPOT)

    out = HistoricalPricer(history).price(
        legs, "iron_condor", "0DTE", spot=_SPOT, as_of=_DAY,
        entry_ts=_TS_0945,
    )
    assert out is not None
    assert round(out["entry_price"], 2) == _IC_EXPECTED_RAW_ENTRY, (
        f"Expected raw entry {_IC_EXPECTED_RAW_ENTRY}, got {out['entry_price']}"
    )
    assert out["max_profit"] == round(_IC_EXPECTED_RAW_ENTRY * 100, 2)
    assert out["max_loss"] == round((CONDOR_WING - _IC_EXPECTED_RAW_ENTRY) * 100, 2)


def test_historical_pricer_without_entry_ts_uses_first_bar():
    """Without entry_ts, the pricer uses iloc[0] (09:30 bar, price=1.0 for all legs).
    IC net = (1.0+1.0) - (1.0+1.0) = 0.0 → non-positive → None.
    This confirms the default path hasn't changed and validates the two-bar setup."""
    history = _make_history_with_two_bars(_IC_PRICES)
    legs = select_legs("iron_condor", spot=_SPOT)

    out = HistoricalPricer(history).price(
        legs, "iron_condor", "0DTE", spot=_SPOT, as_of=_DAY,
        # no entry_ts
    )
    # All legs at price 1.0 → IC credit = (1.0+1.0)-(1.0+1.0) = 0.0 → None
    assert out is None


def test_historical_pricer_entry_ts_debit_marks_correctly():
    """Debit spread: entry_ts marks at the 09:45 bar (real prices)."""
    history = _make_history_with_two_bars(_BD_PRICES)
    legs = select_legs("bull_debit", spot=_SPOT)

    out = HistoricalPricer(history).price(
        legs, "bull_debit", "0DTE", spot=_SPOT, as_of=_BD_DAY,
        entry_ts=_TS_0945,
    )
    assert out is not None
    assert round(out["entry_price"], 2) == _BD_EXPECTED_RAW_ENTRY
    assert out["max_profit"] == round((DEBIT_SHORT_OTM - _BD_EXPECTED_RAW_ENTRY) * 100, 2)
    assert out["max_loss"] == round(_BD_EXPECTED_RAW_ENTRY * 100, 2)


# ── HistoricalPricer.price() with expiry ──────────────────────────────────────

def test_historical_pricer_explicit_expiry_overrides_target_window():
    """When expiry is provided, the pricer must use it for option_ticker lookups
    instead of _target_expiry_window. This is the 1-3DTE parity fix: the backtest
    passes the explicit expiry (day+2) rather than the window start (day+1).
    """
    explicit_expiry = date(2026, 6, 3)   # day+2, NOT day+1 (_target_expiry_window default)
    wrong_expiry = date(2026, 6, 2)      # what _target_expiry_window would give for 1-3DTE

    prices = {
        option_ticker("SPY", explicit_expiry, "C", 500): 2.50,
        option_ticker("SPY", explicit_expiry, "C", 503): 1.00,
        # Keys for wrong_expiry intentionally absent — would cause None if expiry used wrong
        option_ticker("SPY", wrong_expiry, "C", 500): 99.0,
        option_ticker("SPY", wrong_expiry, "C", 503): 99.0,
    }

    class _H:
        def get_aggs(self, contract, *a, **k):
            px = prices.get(contract)
            if px is None:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            return pd.DataFrame(
                {"close": [px]},
                index=[_TS_0945],
            )

    legs = select_legs("bull_debit", spot=_SPOT)
    # as_of = 2026-06-01, dte_bucket = "1-3DTE"
    # Without expiry param: _target_expiry_window gives min=2026-06-02 → wrong prices (99.0)
    # With expiry=explicit_expiry: gets the correct 2.50/1.00 prices
    out = HistoricalPricer(_H()).price(
        legs, "bull_debit", "1-3DTE", spot=_SPOT, as_of=date(2026, 6, 1),
        expiry=explicit_expiry,
    )
    assert out is not None
    assert round(out["entry_price"], 2) == round(2.50 - 1.00, 2)   # 1.50


# ── build_structure forwards entry_ts / expiry ────────────────────────────────

def test_build_structure_forwards_entry_ts_to_pricer():
    """build_structure must forward entry_ts to the pricer's price() call."""
    history = _make_history_with_two_bars(_IC_PRICES)
    out = build_structure(
        "iron_condor", "0DTE", _SPOT,
        HistoricalPricer(history),
        as_of=_DAY,
        entry_ts=_TS_0945,
    )
    assert out is not None
    assert round(out["entry_price"], 2) == _IC_EXPECTED_RAW_ENTRY


def test_build_structure_forwards_expiry_to_pricer():
    """build_structure must forward expiry to the pricer's price() call."""
    explicit_expiry = date(2026, 6, 3)
    prices = {
        option_ticker("SPY", explicit_expiry, "C", 500): 2.50,
        option_ticker("SPY", explicit_expiry, "C", 503): 1.00,
    }

    class _H:
        def get_aggs(self, contract, *a, **k):
            px = prices.get(contract)
            if px is None:
                return pd.DataFrame(columns=["close"])
            return pd.DataFrame({"close": [px]}, index=[_TS_0945])

    out = build_structure(
        "bull_debit", "1-3DTE", _SPOT,
        HistoricalPricer(_H()),
        as_of=date(2026, 6, 1),
        expiry=explicit_expiry,
    )
    assert out is not None
    assert round(out["entry_price"], 2) == 1.50


# ── Backtest entry-pricing parity: inline math == builder output ──────────────

def test_entry_pricing_parity_matches_builder():
    """The backtest's inline entry_px (raw, before slippage) must equal
    build_structure(HistoricalPricer, entry_ts=..., expiry=...).entry_price
    on the same controlled fake.

    The backtest applies slippage AFTER this step, so this test checks only
    the raw mark — not the slippage-adjusted value.
    """
    from backtests.intraday_backtest import _spread_value, is_credit_structure

    legs = select_legs("iron_condor", spot=_SPOT)
    history = _make_history_with_two_bars(_IC_PRICES)

    # Simulate what the backtest does: fetch each leg's series, marks_at(entry_ts)
    oh = _make_history_with_two_bars(_IC_PRICES)
    entry_marks = []
    for leg in legs:
        contract = option_ticker("SPY", _DAY, leg["cp"], leg["strike"])
        df = oh.get_aggs(contract, 5, "minute", _DAY, _DAY)
        s = df["close"]  # already ET-indexed in our fake
        at = s[s.index <= _TS_0945]
        entry_marks.append((leg, float(at.iloc[-1])))

    # The backtest's inline entry_px (raw, before slippage)
    inline_raw = _spread_value(entry_marks, "iron_condor")

    # The builder's entry_price (should be identical)
    built = build_structure(
        "iron_condor", "0DTE", _SPOT,
        HistoricalPricer(_make_history_with_two_bars(_IC_PRICES)),
        as_of=_DAY,
        entry_ts=_TS_0945,
    )
    assert built is not None
    assert round(built["entry_price"], 4) == round(inline_raw, 4), (
        f"Builder entry_price {built['entry_price']} != inline raw {inline_raw}"
    )


def test_entry_pricing_parity_1to3dte():
    """1-3DTE path: explicit expiry is forwarded, entry_ts marks correctly."""
    from backtests.intraday_backtest import _spread_value
    from datetime import timedelta

    spot = 500.0
    day = date(2026, 6, 2)
    expiry = day + timedelta(days=2)   # day+2, matches _simulate_short_dte_with_expiration
    legs = select_legs("bull_debit", spot=spot)

    prices = {
        option_ticker("SPY", expiry, "C", 500): 2.30,
        option_ticker("SPY", expiry, "C", 503): 0.90,
    }
    ts_0945 = pd.Timestamp("2026-06-02 09:45:00", tz="US/Eastern")
    ts_0930 = pd.Timestamp("2026-06-02 09:30:00", tz="US/Eastern")

    class _H:
        def get_aggs(self, contract, *a, **k):
            px = prices.get(contract)
            if px is None:
                return pd.DataFrame(columns=["close"])
            return pd.DataFrame({"close": [1.0, px]}, index=[ts_0930, ts_0945])

    # Inline backtest marks_at(entry_ts)
    oh = _H()
    entry_marks = []
    for leg in legs:
        contract = option_ticker("SPY", expiry, leg["cp"], leg["strike"])
        df = oh.get_aggs(contract, 5, "minute", day, day)
        s = df["close"]
        at = s[s.index <= ts_0945]
        entry_marks.append((leg, float(at.iloc[-1])))
    inline_raw = _spread_value(entry_marks, "bull_debit")

    # Builder output
    built = build_structure(
        "bull_debit", "1-3DTE", spot,
        HistoricalPricer(_H()),
        as_of=day,
        entry_ts=ts_0945,
        expiry=expiry,
    )
    assert built is not None
    assert round(built["entry_price"], 4) == round(inline_raw, 4)
