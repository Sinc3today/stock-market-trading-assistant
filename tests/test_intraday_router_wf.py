"""Tests for backtests/intraday_router_wf.py."""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.intraday_router_wf import _MockBroker


def test_mockbroker_empty_returns_zero_opens():
    broker = _MockBroker()
    assert broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="0DTE") == []
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 0


def test_mockbroker_record_open_visible_to_dedup_queries():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    opens = broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="0DTE")
    assert len(opens) == 1
    assert opens[0]["outcome"] == "open"
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 1


def test_mockbroker_different_combos_isolated():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    assert broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="1-3DTE") == []
    assert broker.trades.get_trades_by(strategy="call_debit_spread", dte_bucket="0DTE") == []


import config
from backtests.intraday_router_wf import _bypass_tier_gate


def test_bypass_tier_gate_lowers_minimum_inside_block():
    original = config.ENTRY_TIER_MINIMUM
    with _bypass_tier_gate():
        assert config.ENTRY_TIER_MINIMUM == "watch"
    assert config.ENTRY_TIER_MINIMUM == original


def test_bypass_tier_gate_restores_on_exception():
    original = config.ENTRY_TIER_MINIMUM
    with pytest.raises(RuntimeError, match="boom"):
        with _bypass_tier_gate():
            raise RuntimeError("boom")
    assert config.ENTRY_TIER_MINIMUM == original


def test_bypass_tier_gate_restores_even_after_nested_change():
    """If user code mutates ENTRY_TIER_MINIMUM inside the block, the original
    value (captured at __enter__) is still restored."""
    original = config.ENTRY_TIER_MINIMUM
    with _bypass_tier_gate():
        config.ENTRY_TIER_MINIMUM = "something_else"  # nasty caller
    assert config.ENTRY_TIER_MINIMUM == original


from backtests.intraday_router_wf import generate_windows


def test_generate_windows_full_2024_2025_monthly_step():
    """6mo train / 3mo test / 1mo step over 2024-01-02 to 2025-12-31."""
    wins = list(generate_windows(date(2024, 1, 2), date(2025, 12, 31),
                                 train_months=6, test_months=3, step_months=1))
    # First test window: months 7-9 of 2024 (after the 6mo train).
    # Last possible test: months 10-12 of 2025 (ends on/before 2025-12-31).
    assert len(wins) == 16, f"expected 16 windows, got {len(wins)}"
    # Train always precedes test, no overlap inside a single window.
    for train_range, test_range in wins:
        assert train_range[1] < test_range[0], \
            f"train must end before test starts: {train_range} vs {test_range}"


def test_generate_windows_monotonic_test_starts():
    """Sliding window: each window's test_start is monotonically increasing."""
    wins = list(generate_windows(date(2024, 1, 2), date(2025, 12, 31)))
    test_starts = [test_range[0] for _, test_range in wins]
    assert test_starts == sorted(test_starts)


def test_generate_windows_stops_when_test_would_overshoot_end():
    """No window whose test_range extends past `end`."""
    end = date(2024, 12, 31)
    wins = list(generate_windows(date(2024, 1, 2), end,
                                 train_months=6, test_months=3, step_months=1))
    for _, (_, test_end) in wins:
        assert test_end <= end


from backtests.intraday_router_wf import (
    _strategy_to_structure,
    STRATEGY_NOT_SUPPORTED,
)


def test_strategy_to_structure_iron_condor():
    assert _strategy_to_structure("iron_condor", "neutral") == "iron_condor"


def test_strategy_to_structure_call_debit_spread_bullish():
    assert _strategy_to_structure("call_debit_spread", "bullish") == "bull_debit"


def test_strategy_to_structure_put_debit_spread_bearish():
    assert _strategy_to_structure("put_debit_spread", "bearish") == "bear_debit"


def test_strategy_to_structure_unknown_returns_sentinel():
    assert _strategy_to_structure("rotational_diagonal", "bullish") is STRATEGY_NOT_SUPPORTED


# simulate_short_dte_day is tested via the integration test in Task 12 —
# unit-testing it would re-test simulate_0dte_day, which already has tests
# in backtests/intraday_backtest.py's own suite.
