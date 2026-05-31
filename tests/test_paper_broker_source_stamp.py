"""
tests/test_paper_broker_source_stamp.py -- both paper-entry paths stamp `source`.

Regression guard for the auto-paper discovery bug: every bot-generated paper
trade must carry the structured `source` field so resolvers/exits recognize it
regardless of how its notes string is formatted.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder import TradeRecorder
from learning.paper_broker import PaperBroker, AUTO_SOURCE, is_auto_paper


def test_log_entry_persists_source(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="iron_condor", strategy="iron_condor",
        source=AUTO_SOURCE,
    )
    t = rec.get_trade_by_id(tid)
    assert t["source"] == AUTO_SOURCE


def test_execute_signal_stamps_auto_source(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = {
        "date": "2026-05-27", "strategy": "iron_condor", "dte_bucket": "0DTE",
        "book": "learning", "direction": "neutral", "entry_price": 2.5,
        "max_profit": 250.0, "max_loss": 250.0, "legs": [],
    }
    result = broker.execute_signal(setup)
    t = TradeRecorder().get_trade_by_id(result["trade_id"])
    assert t["source"] == AUTO_SOURCE
    assert is_auto_paper(t) is True
