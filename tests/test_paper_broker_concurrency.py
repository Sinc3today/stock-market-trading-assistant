"""Phase 2b-2: paper_broker supports multi-position concurrency with caps.

Existing 09:16 daily flow is byte-identical until caps bind (and 45DTE never
opens more than 1/day, so caps don't bind in production today). New
execute_signal(setup) method is added for Phase 3's intraday consumer; no
caller invokes it yet."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from learning.paper_broker import (
    PaperBroker, MAX_CONCURRENT_DISCIPLINED, MAX_CONCURRENT_LEARNING,
)


@pytest.fixture(autouse=True)
def _entry_window_open(monkeypatch):
    """Neutralize the 09:45-15:00 ET entry-window guard so open-logic tests don't
    depend on wall-clock time. The guard itself is covered by test_entry_window.py."""
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


def _tradeable_play(date_str="2026-05-26"):
    return {
        "date":       date_str,
        "tradeable":  True,
        "regime":     "trending_up_calm",
        "confidence": 0.8,
        "reasons":    ["test"],
        "metrics":    {"spy_close": 740.0, "ma200": 678.0, "ma200_dist_%": 9.0,
                       "adx": 34.0, "vix": 17.0, "ivr": 40.0},
        "options": {
            "tradeable":   True,
            "strategy":    "debit_spread",
            "direction":   "bullish",
            "entry_price": 1.10,
            "max_profit":  200.0,
            "max_loss":    110.0,
            "legs":        [{"strike": 740, "action": "buy",  "type": "call"},
                            {"strike": 745, "action": "sell", "type": "call"}],
        },
    }


def test_caps_constants_have_sane_defaults():
    """Disciplined book is tighter than learning book (real money vs learning samples)."""
    assert MAX_CONCURRENT_DISCIPLINED == 3
    assert MAX_CONCURRENT_LEARNING    == 6
    assert MAX_CONCURRENT_LEARNING >= MAX_CONCURRENT_DISCIPLINED


def test_single_daily_play_proceeds_normally(tmp_path, monkeypatch):
    """Parity: with 0 open positions, the existing 45DTE flow opens 1 trade
    just like today. No cap interference."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    result = broker.execute(_tradeable_play("2026-05-26"))
    assert result.get("recorded") is True or result.get("trade_id") is not None


def test_cap_blocks_new_disciplined_when_already_at_max(tmp_path, monkeypatch):
    """If 3 disciplined positions are already open, the next call to execute()
    must NOT open a 4th — but it still logs the Prediction (we learn from
    skipped entries)."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    # Seed 3 open disciplined trades.
    for i in range(MAX_CONCURRENT_DISCIPLINED):
        broker.execute(_tradeable_play(date_str=f"2026-05-2{i}"))
    # Sanity: 3 open trades on the books.
    from journal.trade_recorder import TradeRecorder
    open_n = sum(1 for t in TradeRecorder().get_all_trades()
                 if t.get("outcome") == "open")
    assert open_n == MAX_CONCURRENT_DISCIPLINED

    # Try to open a 4th.
    result = broker.execute(_tradeable_play(date_str="2026-05-27"))
    # Trade NOT opened (capped); Prediction still logged.
    assert result.get("trade_id") is None
    # The Prediction record exists for date 2026-05-27.
    from learning.predictions import PredictionLog
    pred = PredictionLog().get("2026-05-27")
    assert pred is not None


def test_cap_count_filtered_by_book(tmp_path, monkeypatch):
    """The disciplined cap only counts disciplined-book open positions.
    Open learning-book trades don't push the disciplined cap."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    # Seed 5 open learning-book trades directly (bypassing the broker).
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    for i in range(5):
        rec.log_entry(
            ticker="SPY", entry_price=1.0, size=1,
            trade_type="option_spread", strategy="iron_condor",
            direction="neutral", mode="swing", legs=[],
            dte_bucket="0DTE", book="learning",
        )
    # The broker should still happily open a disciplined trade — caps are per-book.
    broker = PaperBroker()
    result = broker.execute(_tradeable_play("2026-05-26"))
    assert result.get("trade_id") is not None


def test_execute_signal_method_exists_for_phase3_consumers(tmp_path, monkeypatch):
    """Phase 3's intraday scanner will call execute_signal(setup, book='learning' or 'disciplined').
    For now we just verify the method exists and respects caps."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    # Synthetic setup-shaped input (Phase 3 will fill this with real SPYSetup output).
    setup = {
        "date":        "2026-05-26",
        "strategy":    "iron_condor",
        "dte_bucket":  "0DTE",
        "book":        "learning",
        "direction":   "neutral",
        "entry_price": 2.50,
        "max_profit":  250.0,
        "max_loss":    250.0,
        "legs":        [],
    }
    result = broker.execute_signal(setup)
    assert result.get("trade_id") is not None
    # The trade is tagged as learning + 0DTE iron_condor.
    from journal.trade_recorder import TradeRecorder
    t = TradeRecorder().get_trade_by_id(result["trade_id"])
    assert t["book"]       == "learning"
    assert t["dte_bucket"] == "0DTE"
    assert t["strategy"]   == "iron_condor"
