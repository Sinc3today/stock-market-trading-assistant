"""
tests/test_trade_recorder.py — Test trade recorder including spread strategies

Run with:
    pytest tests/test_trade_recorder.py -v
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from journal.trade_recorder import TradeRecorder


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return TradeRecorder()


# ─────────────────────────────────────────
# BASIC ENTRY / EXIT
# ─────────────────────────────────────────

def test_log_stock_entry(recorder):
    tid = recorder.log_entry("AAPL", 170.0, 10)
    assert len(tid) == 8
    trade = recorder.get_trade_by_id(tid)
    assert trade["ticker"]  == "AAPL"
    assert trade["outcome"] == "open"
    print(f"\n✅ Stock entry: [{tid}] AAPL @ $170")

def test_log_stock_win(recorder):
    tid = recorder.log_entry("AAPL", 170.0, 10, direction="bullish")
    recorder.log_exit(tid, 182.0)
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"]     == "win"
    assert trade["pnl_dollars"] == 120.0
    print(f"\n✅ Stock win: P&L ${trade['pnl_dollars']}")

def test_log_stock_loss(recorder):
    tid = recorder.log_entry("AAPL", 170.0, 10, direction="bullish")
    recorder.log_exit(tid, 162.0)
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"]     == "loss"
    assert trade["pnl_dollars"] < 0
    print(f"\n✅ Stock loss: P&L ${trade['pnl_dollars']}")

def test_bearish_stock_win(recorder):
    tid = recorder.log_entry("SPY", 450.0, 5, direction="bearish")
    recorder.log_exit(tid, 435.0)
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"] == "win"
    print(f"\n✅ Bearish stock win: P&L ${trade['pnl_dollars']}")


# ─────────────────────────────────────────
# DEBIT SPREAD TESTS
# ─────────────────────────────────────────

def test_debit_spread_entry(recorder):
    legs = [
        {"action": "BUY",  "option_type": "CALL", "strike": 170, "expiry": "2024-03-15"},
        {"action": "SELL", "option_type": "CALL", "strike": 175, "expiry": "2024-03-15"},
    ]
    tid = recorder.log_entry(
        ticker="AAPL", entry_price=2.30, size=2,
        strategy="debit_spread", direction="bullish",
        legs=legs, max_profit=500.0, max_loss=460.0
    )
    trade = recorder.get_trade_by_id(tid)
    assert trade["strategy"]  == "debit_spread"
    assert len(trade["legs"]) == 2
    assert trade["max_profit"] == 500.0
    print(f"\n✅ Debit spread entry: [{tid}] net debit $2.30 × 2 contracts")

def test_debit_spread_win(recorder):
    """Sold spread for more than paid — profit."""
    legs = [
        {"action": "BUY",  "option_type": "CALL", "strike": 170},
        {"action": "SELL", "option_type": "CALL", "strike": 175},
    ]
    tid = recorder.log_entry("AAPL", 2.30, 2, strategy="debit_spread", legs=legs)
    recorder.log_exit(tid, 3.80)   # Sold spread for $3.80 (paid $2.30)
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"]     == "win"
    assert trade["pnl_dollars"] == round((3.80 - 2.30) * 2 * 100, 2)  # $300
    print(f"\n✅ Debit spread win: P&L ${trade['pnl_dollars']} | "
          f"P&L/contract: ${trade['pnl_per_contract']}")

def test_debit_spread_loss(recorder):
    """Spread expired worthless — lost premium paid."""
    tid = recorder.log_entry("AAPL", 2.30, 1, strategy="debit_spread")
    recorder.log_exit(tid, 0.20)  # Nearly expired worthless
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"]     == "loss"
    assert trade["pnl_dollars"] == round((0.20 - 2.30) * 1 * 100, 2)  # -$210
    print(f"\n✅ Debit spread loss: P&L ${trade['pnl_dollars']}")


# ─────────────────────────────────────────
# CREDIT SPREAD TESTS
# ─────────────────────────────────────────

def test_credit_spread_entry(recorder):
    legs = [
        {"action": "SELL", "option_type": "PUT", "strike": 165},
        {"action": "BUY",  "option_type": "PUT", "strike": 160},
    ]
    tid = recorder.log_entry(
        ticker="AAPL", entry_price=1.80, size=1,
        strategy="credit_spread", direction="bullish",
        legs=legs, max_profit=180.0, max_loss=320.0
    )
    trade = recorder.get_trade_by_id(tid)
    assert trade["strategy"] == "credit_spread"
    print(f"\n✅ Credit spread entry: [{tid}] net credit $1.80")

def test_credit_spread_win(recorder):
    """Bought back for less than received — profit."""
    tid = recorder.log_entry("AAPL", 1.80, 1, strategy="credit_spread")
    recorder.log_exit(tid, 0.40)   # Bought back for $0.40 (received $1.80)
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"]     == "win"
    assert trade["pnl_dollars"] == round((1.80 - 0.40) * 1 * 100, 2)  # $140
    print(f"\n✅ Credit spread win: P&L ${trade['pnl_dollars']}")


# ─────────────────────────────────────────
# IRON CONDOR TESTS
# ─────────────────────────────────────────

def test_iron_condor_entry(recorder):
    legs = [
        {"action": "SELL", "option_type": "PUT",  "strike": 430},
        {"action": "BUY",  "option_type": "PUT",  "strike": 425},
        {"action": "SELL", "option_type": "CALL", "strike": 470},
        {"action": "BUY",  "option_type": "CALL", "strike": 475},
    ]
    tid = recorder.log_entry(
        ticker="SPY", entry_price=2.40, size=1,
        strategy="iron_condor", direction="neutral",
        legs=legs, max_profit=240.0, max_loss=260.0
    )
    trade = recorder.get_trade_by_id(tid)
    assert trade["strategy"]  == "iron_condor"
    assert len(trade["legs"]) == 4
    print(f"\n✅ Iron condor entry: [{tid}] 4 legs, $2.40 credit")

def test_iron_condor_win(recorder):
    """Price stayed in range — bought back cheap."""
    tid = recorder.log_entry("SPY", 2.40, 1, strategy="iron_condor")
    recorder.log_exit(tid, 0.60)
    trade = recorder.get_trade_by_id(tid)
    assert trade["outcome"]     == "win"
    assert trade["pnl_dollars"] == round((2.40 - 0.60) * 1 * 100, 2)  # $180
    print(f"\n✅ Iron condor win: P&L ${trade['pnl_dollars']}")


# ─────────────────────────────────────────
# RETRIEVAL + SUMMARY
# ─────────────────────────────────────────

def test_invalid_trade_id_returns_false(recorder):
    assert recorder.log_exit("FAKEID", 180.0) is False
    print(f"\n✅ Invalid ID handled gracefully")

def test_get_open_and_closed(recorder):
    t1 = recorder.log_entry("AAPL", 170.0, 10)
    t2 = recorder.log_entry("MSFT", 380.0, 5)
    recorder.log_exit(t1, 182.0)
    assert len(recorder.get_open_trades())   == 1
    assert len(recorder.get_closed_trades()) == 1
    print(f"\n✅ Open/closed split correct")

def test_summary_stats_mixed_strategies(recorder):
    t1 = recorder.log_entry("AAPL", 170.0, 10, strategy="stock")
    t2 = recorder.log_entry("AAPL", 2.30,  2,  strategy="debit_spread")
    t3 = recorder.log_entry("SPY",  1.80,  1,  strategy="credit_spread")
    recorder.log_exit(t1, 182.0)   # win
    recorder.log_exit(t2, 3.80)    # win
    recorder.log_exit(t3, 0.40)    # win
    stats = recorder.get_summary_stats()
    assert stats["wins"]     == 3
    assert stats["win_rate"] == 100.0
    print(f"\n✅ Mixed strategy stats: {stats['wins']} wins | "
          f"Total P&L ${stats['total_pnl']}")