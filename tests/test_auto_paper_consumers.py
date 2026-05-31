"""
tests/test_auto_paper_consumers.py -- resolvers honor the structured source field.

A trade carrying source="auto-paper" but a notes string WITHOUT the legacy
[AUTO-PAPER] tag must still be seen by the EOD resolvers. Pre-fix these filtered
on the notes substring and would skip such a trade (the original bug class).
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder   import TradeRecorder
from learning.paper_broker     import AUTO_SOURCE
from learning.expiry_resolver  import ExpiryResolver
from learning.outcome_resolver import OutcomeResolver


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _auto_trade_no_tag(rec: TradeRecorder, legs) -> str:
    # source set, but notes deliberately carry NO [AUTO-PAPER] substring.
    return rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="iron_condor", strategy="iron_condor",
        direction="neutral", mode="intraday", legs=legs,
        notes="event-driven entry 2026-05-27", source=AUTO_SOURCE,
    )


def test_expiry_resolver_closes_source_only_trade(iso):
    rec = TradeRecorder()
    legs = [
        {"action": "SELL", "type": "put",  "strike": 740, "expiration": "2026-05-27"},
        {"action": "BUY",  "type": "put",  "strike": 735, "expiration": "2026-05-27"},
    ]
    tid = _auto_trade_no_tag(rec, legs)
    closed = ExpiryResolver(trade_recorder=rec).resolve_expired(
        today=date(2026, 5, 30), spy_close=757.0
    )
    assert tid in [c["trade_id"] for c in closed]
    assert rec.get_trade_by_id(tid)["outcome"] != "open"


def test_outcome_resolver_snapshots_source_only_trade(iso):
    rec = TradeRecorder()
    legs = [{"action": "SELL", "type": "put", "strike": 740, "expiration": "2026-06-26"}]
    tid = _auto_trade_no_tag(rec, legs)
    OutcomeResolver(trade_recorder=rec)._snapshot_open_paper_trades("2026-05-30", 757.0)
    assert "[MTM 2026-05-30]" in rec.get_trade_by_id(tid)["notes_entry"]
