"""tests/test_concentration.py -- short-strike proximity guard.

Audit T1.2: 3 condors were open with short puts within $13 and a doubled short
call — a single -3% day breaches all of them, and only a COUNT cap existed. The
guard skips a new auto-entry when any of its short strikes lands within
CONCENTRATION_GUARD_PCT of an existing open short strike (disciplined + live).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _open_condor(sp=700.0, sc=771.0, book="live"):
    return {"trade_id": "X", "book": book, "outcome": "open",
            "legs": [
                {"action": "SELL", "option_type": "PUT",  "strike": sp},
                {"action": "BUY",  "option_type": "PUT",  "strike": sp - 5},
                {"action": "SELL", "option_type": "CALL", "strike": sc},
                {"action": "BUY",  "option_type": "CALL", "strike": sc + 5},
            ]}


def _new_legs(sp=705.0, sc=790.0):
    return [
        {"action": "SELL", "option_type": "PUT",  "strike": sp},
        {"action": "BUY",  "option_type": "PUT",  "strike": sp - 5},
        {"action": "SELL", "option_type": "CALL", "strike": sc},
        {"action": "BUY",  "option_type": "CALL", "strike": sc + 5},
    ]


def test_conflict_when_new_short_near_existing_short():
    from signals.concentration import proximity_conflicts
    # new short put 705 vs existing 700 -> 0.71% apart < 1.5% -> conflict
    c = proximity_conflicts(_new_legs(sp=705, sc=790), [_open_condor()], pct=1.5)
    assert len(c) == 1
    assert c[0]["new_strike"] == 705 and c[0]["existing_strike"] == 700


def test_no_conflict_when_far_apart():
    from signals.concentration import proximity_conflicts
    # new short put 660 (5.7% below 700), call 800 (3.8% above 771) -> clear
    assert proximity_conflicts(_new_legs(sp=660, sc=800), [_open_condor()], pct=1.5) == []


def test_same_type_only_puts_vs_puts_calls_vs_calls():
    from signals.concentration import proximity_conflicts
    # new short CALL at 700 near existing short PUT 700 must NOT conflict
    legs = [{"action": "SELL", "option_type": "CALL", "strike": 700}]
    assert proximity_conflicts(legs, [_open_condor(sp=700, sc=880)], pct=1.5) == []


def test_ignores_closed_and_long_legs():
    from signals.concentration import proximity_conflicts
    closed = _open_condor(); closed["outcome"] = "win"
    assert proximity_conflicts(_new_legs(sp=705), [closed], pct=1.5) == []
    # existing LONG put at 705 is protection, not risk -> no conflict
    open_t = {"book": "live", "outcome": "open",
              "legs": [{"action": "BUY", "option_type": "PUT", "strike": 705}]}
    assert proximity_conflicts(_new_legs(sp=705), [open_t], pct=1.5) == []


def test_book_concentration_reports_existing_clusters_once():
    from signals.concentration import book_concentration
    a = _open_condor(sp=700, sc=771); a["trade_id"] = "A"
    b = _open_condor(sp=705, sc=790); b["trade_id"] = "B"
    clusters = book_concentration([a, b], pct=1.5)
    # 700 vs 705 puts cluster, reported exactly once (not once per direction)
    assert len(clusters) == 1
    assert clusters[0]["type"] == "P"


def test_broker_skips_on_concentration(tmp_path, monkeypatch):
    # end-to-end: paper broker must NOT open when the guard fires
    import config
    from datetime import date
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)
    from journal.trade_recorder import TradeRecorder
    from learning.paper_broker import PaperBroker
    # seed an open live condor short put 700
    TradeRecorder().log_entry(ticker="SPY", entry_price=1.5, size=1,
                              trade_type="iron_condor", strategy="iron_condor",
                              direction="neutral", legs=_open_condor()["legs"],
                              book="live")
    play = {"date": date.today().isoformat(), "tradeable": True,
            "regime": "choppy_low_vol", "confidence": 0.8, "reasons": [],
            "metrics": {"spy_close": 740.0},
            "options": {"strategy": "iron_condor",
                        "legs": _new_legs(sp=703, sc=790),   # 703 vs 700 -> conflict
                        "net_credit": 1.4}}
    result = PaperBroker().execute(play)
    assert result["recorded"] is False
    assert "concentration" in str(result.get("skipped") or result.get("skipped_reason") or "")
