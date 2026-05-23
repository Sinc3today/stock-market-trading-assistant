"""Phase 2a: TradeRecorder.log_exit accepts an optional structured exit_reason
field. TradeRecorder.get_trades_by(...) filters by the Task 1 tags."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder import TradeRecorder


def _seed(rec):
    """Open + close three trades across different sub-strategies."""
    t1 = rec.log_entry(ticker="SPY", entry_price=1.10, size=1,
                       trade_type="option_spread", strategy="debit_spread",
                       direction="bullish", mode="swing", legs=[],
                       dte_bucket="45DTE", book="disciplined")
    t2 = rec.log_entry(ticker="SPY", entry_price=2.50, size=1,
                       trade_type="option_spread", strategy="iron_condor",
                       direction="neutral", mode="swing", legs=[],
                       dte_bucket="0DTE", book="learning")
    t3 = rec.log_entry(ticker="SPY", entry_price=0.80, size=1,
                       trade_type="option_spread", strategy="debit_spread",
                       direction="bullish", mode="swing", legs=[],
                       dte_bucket="45DTE", book="disciplined")
    rec.log_exit(t1, exit_price=2.00, exit_reason="target")
    rec.log_exit(t2, exit_price=2.00, exit_reason="stop")
    # t3 left open
    return t1, t2, t3


def test_log_exit_accepts_and_persists_exit_reason(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    t1, t2, _ = _seed(rec)
    closed1 = rec.get_trade_by_id(t1)
    closed2 = rec.get_trade_by_id(t2)
    assert closed1["exit_reason"] == "target"
    assert closed2["exit_reason"] == "stop"


def test_log_exit_without_reason_leaves_field_none(tmp_path, monkeypatch):
    """Existing log_exit callers that don't pass exit_reason still work."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(ticker="SPY", entry_price=1.0, size=1,
                        trade_type="option_spread", strategy="iron_condor",
                        direction="neutral", mode="swing", legs=[])
    rec.log_exit(tid, exit_price=0.50)   # no exit_reason
    t = rec.get_trade_by_id(tid)
    assert t.get("exit_reason") is None


def test_get_trades_by_filters_strategy(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    condors = rec.get_trades_by(strategy="iron_condor")
    assert len(condors) == 1
    assert condors[0]["strategy"] == "iron_condor"
    debits = rec.get_trades_by(strategy="debit_spread")
    assert len(debits) == 2


def test_get_trades_by_filters_book_and_dte_bucket(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    learning = rec.get_trades_by(book="learning")
    assert [t["strategy"] for t in learning] == ["iron_condor"]
    dte0 = rec.get_trades_by(dte_bucket="0DTE")
    assert len(dte0) == 1
    combined = rec.get_trades_by(strategy="debit_spread",
                                  dte_bucket="45DTE", book="disciplined")
    assert len(combined) == 2


def test_get_trades_by_no_filter_returns_all(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    assert len(rec.get_trades_by()) == 3


def test_get_trades_by_filters_exit_reason(tmp_path, monkeypatch):
    """Filtering by exit_reason — useful for 'which trades hit target?'"""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    targets = rec.get_trades_by(exit_reason="target")
    assert len(targets) == 1
    stops = rec.get_trades_by(exit_reason="stop")
    assert len(stops) == 1
