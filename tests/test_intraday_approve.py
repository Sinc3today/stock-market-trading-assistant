"""tests/test_intraday_approve.py -- 1-3DTE opens fire the emergency approve (2026-07-09)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_disciplined_open_prefers_emergency_approve(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    import scanners.intraday_scanner as sc
    tid = TradeRecorder().log_entry(
        ticker="SPY", entry_price=2.1, size=1, trade_type="iron_condor",
        strategy="iron_condor", direction="neutral",
        legs=[{"action": "SELL", "option_type": "PUT", "strike": 752, "expiry": "2026-07-11"}],
        dte_bucket="1-3DTE", book="disciplined")
    approved, played = [], []
    monkeypatch.setattr(sc, "_APPROVE_FN", lambda t: approved.append(t["trade_id"]))
    monkeypatch.setattr(sc, "_PLAY_FN", lambda **k: played.append(k))
    sc._maybe_play_on_open({"book": "disciplined", "strategy": "iron_condor",
                            "dte_bucket": "1-3DTE"}, {"recorded": True, "trade_id": tid})
    assert approved == [tid] and played == []      # emergency approve wins


def test_learning_book_opens_stay_silent(monkeypatch):
    import scanners.intraday_scanner as sc
    called = []
    monkeypatch.setattr(sc, "_APPROVE_FN", lambda t: called.append(1))
    monkeypatch.setattr(sc, "_PLAY_FN", lambda **k: called.append(1))
    sc._maybe_play_on_open({"book": "learning"}, {"recorded": True, "trade_id": "X"})
    assert called == []
