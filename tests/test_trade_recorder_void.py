"""
tests/test_trade_recorder_void.py -- voiding stub/erroneous trades.

A voided trade is one that was never a real fill (e.g. a Phase 3 synthetic
stub). It must be removed from the open set WITHOUT polluting performance:
no P&L, not counted as a win or a loss, not in the closed-trade denominator.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder import TradeRecorder


@pytest.fixture
def rec(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return TradeRecorder()


def test_void_marks_outcome_and_clears_pnl(rec):
    tid = rec.log_entry(ticker="SPY", entry_price=1.0, size=1,
                        trade_type="iron_condor", strategy="iron_condor")
    rec.void_trade(tid, reason="pre-Phase-4b stub, not priceable")
    t = rec.get_trade_by_id(tid)
    assert t["outcome"] == "void"
    assert t["pnl_dollars"] is None
    assert "pre-Phase-4b stub" in t["notes_exit"]


def test_void_not_in_open_or_closed(rec):
    tid = rec.log_entry(ticker="SPY", entry_price=1.0, size=1,
                        trade_type="iron_condor", strategy="iron_condor")
    rec.void_trade(tid, reason="stub")
    assert tid not in [t["trade_id"] for t in rec.get_open_trades()]
    assert tid not in [t["trade_id"] for t in rec.get_closed_trades()]


def test_void_excluded_from_summary_stats(rec):
    # One genuine winning closed trade...
    win = rec.log_entry(ticker="SPY", entry_price=1.0, size=1,
                        trade_type="single_leg", strategy="single_leg",
                        legs=[{"action": "BUY", "type": "call", "strike": 500}])
    rec.log_exit(trade_id=win, exit_price=2.0)
    # ...plus a stub we void.
    stub = rec.log_entry(ticker="SPY", entry_price=1.0, size=1,
                         trade_type="iron_condor", strategy="iron_condor")
    rec.void_trade(stub, reason="stub")

    s = rec.get_summary_stats()
    assert s["closed"] == 1          # only the real trade counts
    assert s["wins"] == 1
    assert s["win_rate"] == 100.0    # void must not drag this down
