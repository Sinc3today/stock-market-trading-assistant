"""
tests/test_learning_expiry_resolver.py -- ExpiryResolver intrinsic-value close.

Isolates trades.json to tmp_path. Covers:
  - Intrinsic computation per leg shape
  - Strategy-specific exit_price sign
  - Closes only AUTO-PAPER trades, only after expiry
  - Idempotent (re-run leaves closed trades alone)
  - format_expiry_message handles empty + populated lists
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder       import TradeRecorder
from learning.expiry_resolver     import ExpiryResolver, format_expiry_message
from learning.paper_broker        import AUTO_TAG


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _record_auto_paper(
    trades:     TradeRecorder,
    strategy:   str,
    direction:  str,
    entry_px:   float,
    legs:       list[dict],
    max_loss:   float | None = None,
) -> str:
    return trades.log_entry(
        ticker          = "SPY",
        entry_price     = entry_px,
        size            = 1,
        trade_type      = strategy,
        strategy        = strategy,
        direction       = direction,
        mode            = "swing",
        legs            = legs,
        max_profit      = None,
        max_loss        = max_loss,
        alert_timestamp = "2026-05-15",
        alert_score     = 70,
        notes           = f"{AUTO_TAG} test",
    )


# ── INTRINSIC HELPERS ─────────────────────────────────

def test_intrinsic_call_long_only():
    legs = [{"action": "BUY", "type": "call", "strike": 500}]
    lv, sv = ExpiryResolver._intrinsic(legs, spy=510.0)
    assert lv == 10.0
    assert sv == 0.0


def test_intrinsic_put_long_only():
    legs = [{"action": "BUY", "type": "put", "strike": 500}]
    lv, sv = ExpiryResolver._intrinsic(legs, spy=495.0)
    assert lv == 5.0
    assert sv == 0.0


def test_intrinsic_iron_condor_all_otm():
    legs = [
        {"action": "BUY",  "type": "put",  "strike": 480},
        {"action": "SELL", "type": "put",  "strike": 490},
        {"action": "SELL", "type": "call", "strike": 510},
        {"action": "BUY",  "type": "call", "strike": 520},
    ]
    lv, sv = ExpiryResolver._intrinsic(legs, spy=500.0)
    assert lv == 0.0 and sv == 0.0


def test_intrinsic_iron_condor_breached_call_side():
    legs = [
        {"action": "BUY",  "type": "put",  "strike": 480},
        {"action": "SELL", "type": "put",  "strike": 490},
        {"action": "SELL", "type": "call", "strike": 510},
        {"action": "BUY",  "type": "call", "strike": 520},
    ]
    lv, sv = ExpiryResolver._intrinsic(legs, spy=515.0)
    # long call (520) OTM = 0; short call (510) ITM = 5
    assert lv == 0.0 and sv == 5.0


def test_intrinsic_uses_option_type_alias():
    """legacy 'option_type' field works the same as 'type'."""
    legs = [{"action": "BUY", "option_type": "call", "strike": 500}]
    lv, sv = ExpiryResolver._intrinsic(legs, spy=510.0)
    assert lv == 10.0


# ── EXIT PRICE PER STRATEGY ──────────────────────────

def test_exit_price_debit_spread_uses_long_minus_short():
    legs = [
        {"action": "BUY",  "type": "call", "strike": 500},
        {"action": "SELL", "type": "call", "strike": 510},
    ]
    # spy=505 → long=5, short=0 → exit=5
    assert ExpiryResolver._exit_price("debit_spread", legs, 505.0) == 5.0


def test_exit_price_credit_spread_clamps_at_zero():
    """If a credit spread expires fully OTM, exit price is $0 (max profit)."""
    legs = [
        {"action": "SELL", "type": "put", "strike": 500},
        {"action": "BUY",  "type": "put", "strike": 495},
    ]
    # spy=510 above both strikes → both OTM → cost to close = 0
    assert ExpiryResolver._exit_price("credit_spread", legs, 510.0) == 0.0


def test_exit_price_credit_spread_max_loss_at_breach():
    legs = [
        {"action": "SELL", "type": "put", "strike": 500},
        {"action": "BUY",  "type": "put", "strike": 495},
    ]
    # spy=490 → short=10, long=5 → cost to close = 5
    assert ExpiryResolver._exit_price("credit_spread", legs, 490.0) == 5.0


def test_exit_price_iron_condor_at_expiry_zero():
    legs = [
        {"action": "BUY",  "type": "put",  "strike": 480},
        {"action": "SELL", "type": "put",  "strike": 490},
        {"action": "SELL", "type": "call", "strike": 510},
        {"action": "BUY",  "type": "call", "strike": 520},
    ]
    assert ExpiryResolver._exit_price("iron_condor", legs, 500.0) == 0.0


# ── NEAREST EXPIRATION ────────────────────────────────

def test_nearest_expiration_picks_earliest():
    legs = [
        {"action": "BUY", "type": "call", "strike": 500, "expiration": "2026-06-19"},
        {"action": "SELL","type": "call", "strike": 510, "expiration": "2026-05-15"},
    ]
    assert ExpiryResolver._nearest_expiration(legs) == date(2026, 5, 15)


def test_nearest_expiration_returns_none_when_missing():
    assert ExpiryResolver._nearest_expiration([{"strike": 500}]) is None


# ── END-TO-END ────────────────────────────────────────

def test_resolves_expired_credit_spread_winner(iso_dirs):
    trades = TradeRecorder()
    legs = [
        {"action": "SELL", "type": "put", "strike": 500, "expiration": "2026-05-15"},
        {"action": "BUY",  "type": "put", "strike": 495, "expiration": "2026-05-15"},
    ]
    tid = _record_auto_paper(trades, "credit_spread", "BULLISH",
                             entry_px=1.50, legs=legs, max_loss=350.0)
    closed = ExpiryResolver(trade_recorder=trades).resolve_expired(
        today=date(2026, 5, 15), spy_close=510.0,
    )
    assert len(closed) == 1
    assert closed[0]["trade_id"] == tid
    assert closed[0]["exit_price"] == 0.0
    # entry 1.50 credit, paid 0 to close → +$150 win
    assert closed[0]["pnl_dollars"] == 150.0
    assert closed[0]["outcome"] == "win"


def test_resolves_expired_debit_spread_loser(iso_dirs):
    trades = TradeRecorder()
    legs = [
        {"action": "BUY",  "type": "call", "strike": 500, "expiration": "2026-05-15"},
        {"action": "SELL", "type": "call", "strike": 510, "expiration": "2026-05-15"},
    ]
    tid = _record_auto_paper(trades, "debit_spread", "BULLISH",
                             entry_px=3.00, legs=legs)
    closed = ExpiryResolver(trade_recorder=trades).resolve_expired(
        today=date(2026, 5, 15), spy_close=495.0,
    )
    assert len(closed) == 1
    # both OTM at expiry, sold for 0 → -$300 loss
    assert closed[0]["exit_price"] == 0.0
    assert closed[0]["pnl_dollars"] == -300.0
    assert closed[0]["outcome"] == "loss"


def test_does_not_close_unexpired_trades(iso_dirs):
    trades = TradeRecorder()
    legs = [
        {"action": "BUY", "type": "call", "strike": 500, "expiration": "2026-06-19"},
    ]
    _record_auto_paper(trades, "single_leg", "BULLISH",
                       entry_px=5.00, legs=legs)
    closed = ExpiryResolver(trade_recorder=trades).resolve_expired(
        today=date(2026, 5, 15), spy_close=505.0,
    )
    assert closed == []
    assert trades.get_open_trades()[0]["outcome"] == "open"


def test_skips_non_auto_paper_trades(iso_dirs):
    trades = TradeRecorder()
    legs = [{"action": "BUY", "type": "call", "strike": 500, "expiration": "2026-05-15"}]
    # Same shape but no AUTO_TAG in notes → must be left alone
    trades.log_entry(
        ticker="SPY", entry_price=5.0, size=1,
        trade_type="single_leg", strategy="single_leg",
        direction="BULLISH", mode="swing",
        legs=legs, max_profit=None, max_loss=None,
        alert_timestamp="2026-05-15", alert_score=70,
        notes="manual entry — no AUTO tag",
    )
    closed = ExpiryResolver(trade_recorder=trades).resolve_expired(
        today=date(2026, 5, 15), spy_close=510.0,
    )
    assert closed == []


def test_idempotent_second_run_closes_nothing(iso_dirs):
    trades = TradeRecorder()
    legs = [
        {"action": "SELL", "type": "put", "strike": 500, "expiration": "2026-05-15"},
        {"action": "BUY",  "type": "put", "strike": 495, "expiration": "2026-05-15"},
    ]
    _record_auto_paper(trades, "credit_spread", "BULLISH",
                       entry_px=1.50, legs=legs, max_loss=350.0)
    r = ExpiryResolver(trade_recorder=trades)
    first  = r.resolve_expired(today=date(2026, 5, 15), spy_close=510.0)
    second = r.resolve_expired(today=date(2026, 5, 15), spy_close=510.0)
    assert len(first)  == 1
    assert len(second) == 0   # already closed, outcome != open


def test_no_spy_close_and_no_polygon_returns_empty(iso_dirs):
    trades = TradeRecorder()
    legs = [{"action": "BUY", "type": "call", "strike": 500, "expiration": "2026-05-15"}]
    _record_auto_paper(trades, "single_leg", "BULLISH",
                       entry_px=5.00, legs=legs)
    closed = ExpiryResolver(trade_recorder=trades).resolve_expired(
        today=date(2026, 5, 15),  # spy_close left None, no polygon injected
    )
    assert closed == []


# ── FORMATTER ─────────────────────────────────────────

def test_format_expiry_message_empty():
    assert format_expiry_message([]) == ""


def test_format_expiry_message_populated():
    msg = format_expiry_message([{
        "trade_id": "ABC12345", "strategy": "credit_spread",
        "expiration": "2026-05-15", "exit_price": 0.0,
        "pnl_dollars": 150.0, "outcome": "win",
    }])
    assert "Paper expiries closed (1)" in msg
    assert "ABC12345" in msg
    assert "credit_spread" in msg
    assert "+150" in msg or "$150" in msg or "+$150" in msg
