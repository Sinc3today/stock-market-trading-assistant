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
