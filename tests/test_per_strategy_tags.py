"""Phase 2a: Prediction + TradeRecorder + paper_broker carry strategy /
dte_bucket / book tags so per-sub-strategy edge measurement is possible.

All new fields default to None; old records deserialize unchanged."""

import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from learning.predictions import Prediction, PredictionLog
from journal.trade_recorder import TradeRecorder


@pytest.fixture(autouse=True)
def _entry_window_open(monkeypatch):
    """Neutralize the 09:45-15:00 ET open gate so open-logic tests don't depend
    on wall-clock time. The gate itself is covered by test_entry_window.py."""
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


# ── Prediction dataclass ──────────────────────────────────────────────────

def test_prediction_new_fields_default_to_none():
    p = Prediction(date="2026-05-26", regime="trending_up_calm",
                   direction="bullish", tradeable=True)
    assert p.strategy   is None
    assert p.dte_bucket is None
    assert p.book       is None


def test_prediction_new_fields_accept_strings():
    p = Prediction(date="2026-05-26", regime="trending_up_calm",
                   direction="bullish", tradeable=True,
                   strategy="iron_condor", dte_bucket="0DTE", book="learning")
    assert p.strategy   == "iron_condor"
    assert p.dte_bucket == "0DTE"
    assert p.book       == "learning"


def test_prediction_roundtrip_through_jsonl(tmp_path, monkeypatch):
    """Old (untagged) prediction entries must still deserialize."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    log = PredictionLog()
    log.save(Prediction(date="2026-05-26", regime="trending_up_calm",
                        direction="bullish", tradeable=True,
                        strategy="bull_debit", dte_bucket="45DTE",
                        book="disciplined"))
    log.save(Prediction(date="2026-05-27", regime="choppy_low_vol",
                        direction="neutral", tradeable=True))   # untagged
    rows = log.all()
    assert len(rows) == 2
    tagged = next(r for r in rows if r["date"] == "2026-05-26")
    untagged = next(r for r in rows if r["date"] == "2026-05-27")
    assert tagged["strategy"]    == "bull_debit"
    assert tagged["dte_bucket"]  == "45DTE"
    assert tagged["book"]        == "disciplined"
    assert untagged.get("strategy")   is None
    assert untagged.get("dte_bucket") is None
    assert untagged.get("book")       is None


# ── TradeRecorder.log_entry ───────────────────────────────────────────────

def test_log_entry_accepts_dte_bucket_and_book_kwargs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(
        ticker="SPY", entry_price=2.50, size=1,
        trade_type="option_spread", strategy="iron_condor",
        direction="neutral", mode="swing",
        legs=[], max_profit=250.0, max_loss=250.0,
        dte_bucket="0DTE", book="learning",
    )
    assert tid
    trade = rec.get_trade_by_id(tid)
    assert trade["dte_bucket"] == "0DTE"
    assert trade["book"]       == "learning"


def test_log_entry_defaults_new_fields_to_none(tmp_path, monkeypatch):
    """Existing callers that don't pass dte_bucket/book still work."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(
        ticker="SPY", entry_price=1.10, size=1,
        trade_type="option_spread", strategy="debit_spread",
        direction="bullish", mode="swing", legs=[],
    )
    trade = rec.get_trade_by_id(tid)
    assert trade.get("dte_bucket") is None
    assert trade.get("book")       is None


# ── paper_broker integration ──────────────────────────────────────────────

def test_paper_broker_populates_45dte_disciplined_tags(tmp_path, monkeypatch):
    """paper_broker, when it writes a Prediction + trade entry, must populate
    dte_bucket='45DTE' and book='disciplined' since that's all the bot currently
    produces. Phase 3's intraday brokers will populate other values."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")

    from learning.paper_broker import PaperBroker
    broker = PaperBroker()
    play = {
        "date":       "2026-05-26",
        "tradeable":  True,
        "regime":     "trending_up_calm",
        "confidence": 0.8,
        "reasons":    ["trend intact"],
        "metrics":    {"spy_close": 740.0, "ma200": 678.0, "ma200_dist_%": 9.0,
                       "adx": 34.0, "vix": 17.0, "ivr": 40.0},
        "options": {
            "tradeable":     True,
            "strategy":      "debit_spread",
            "direction":     "bullish",
            "entry_price":   1.10,
            "max_profit":    200.0,
            "max_loss":      110.0,
            "legs":          [{"strike": 740, "action": "buy",  "type": "call"},
                              {"strike": 745, "action": "sell", "type": "call"}],
        },
    }
    broker.execute(play)

    # Verify the Prediction record was tagged.
    from learning.predictions import PredictionLog
    pred = PredictionLog().get("2026-05-26")
    assert pred is not None
    assert pred["dte_bucket"] == "45DTE"
    assert pred["book"]       == "disciplined"

    # Verify the trade record was tagged.
    from journal.trade_recorder import TradeRecorder
    trades = TradeRecorder().get_all_trades()
    assert len(trades) >= 1
    t = trades[-1]
    assert t["dte_bucket"] == "45DTE"
    assert t["book"]       == "disciplined"
