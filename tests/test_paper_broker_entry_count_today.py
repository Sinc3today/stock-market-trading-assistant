"""Phase 3: PaperBroker._entry_count_today_by_combo counts today's opens
per (strategy, dte_bucket). Restart-safe (reads from persistent TradeRecorder)."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
import pytz

from learning.paper_broker import PaperBroker
from journal.trade_recorder import TradeRecorder

EASTERN = pytz.timezone("US/Eastern")


def _seed_trade(rec, strategy, dte_bucket, book="disciplined"):
    """Helper: open one trade with the given combo."""
    return rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="option_spread", strategy=strategy,
        direction="bullish", mode="intraday", legs=[],
        max_profit=200.0, max_loss=100.0,
        notes="[AUTO-PAPER] test", dte_bucket=dte_bucket, book=book,
    )


def test_entry_count_zero_for_combo_never_opened(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    assert broker._entry_count_today_by_combo("call_debit_spread", "0DTE") == 0


def test_entry_count_counts_today_opens(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    _seed_trade(rec, "call_debit_spread", "0DTE")
    _seed_trade(rec, "call_debit_spread", "0DTE")
    # Same combo, different combo, different strategy — only the same combo counts.
    _seed_trade(rec, "call_debit_spread", "1-3DTE")
    _seed_trade(rec, "iron_condor",       "0DTE")
    assert broker._entry_count_today_by_combo("call_debit_spread", "0DTE") == 2
    assert broker._entry_count_today_by_combo("call_debit_spread", "1-3DTE") == 1
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 1


def test_entry_count_uses_ET_date(tmp_path, monkeypatch):
    """'Today' is today in US/Eastern (matches the rest of the bot's market-hours logic)."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    _seed_trade(rec, "iron_condor", "0DTE")
    # A trade just opened — should count as "today" regardless of local timezone quirks.
    n = broker._entry_count_today_by_combo("iron_condor", "0DTE")
    assert n == 1
