# tests/test_reflector_substrategy.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _trade(strategy, dte_bucket, book, entry_date, outcome="open"):
    return {"strategy": strategy, "dte_bucket": dte_bucket, "book": book,
            "entry_date": entry_date, "outcome": outcome,
            "notes_entry": "[AUTO-PAPER] x", "source": "auto-paper"}


def test_active_substrategies_from_today(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    trades = [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("iron_condor", "0DTE", "learning",    "2026-06-01 09:35 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "disciplined", "2026-06-01 10:00 AM EST"),
        _trade("iron_condor", "45DTE", "disciplined", "2026-05-18 09:16 AM EST"),  # not today
    ]
    active = r._active_substrategies(trades, "2026-06-01")
    assert active == {("iron_condor", "0DTE"), ("call_debit_spread", "1-3DTE")}


def test_scoped_context_includes_both_books(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    trades = [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("iron_condor", "0DTE", "learning",    "2026-06-01 09:35 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "disciplined", "2026-06-01 10:00 AM EST"),
    ]
    ctx = r._build_substrategy_context("iron_condor", "0DTE", trades,
                                       accuracy={"iron_condor:0DTE:disciplined": {"n": 1},
                                                 "iron_condor:0DTE:learning": {"n": 1},
                                                 "call_debit_spread:1-3DTE:disciplined": {"n": 1}},
                                       today_str="2026-06-01")
    # only this combo's trades, both books
    assert len(ctx["trades"]) == 2
    assert set(ctx["accuracy"].keys()) == {"iron_condor:0DTE:disciplined", "iron_condor:0DTE:learning"}
    assert ctx["strategy"] == "iron_condor" and ctx["dte_bucket"] == "0DTE"


def test_substrategy_prompt_has_disconfirmation_and_scope(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector, REFLECTOR_SYSTEM
    # System prompt instructs a disconfirmation pass + stance on entries
    assert "disprove" in REFLECTOR_SYSTEM.lower() or "disconfirm" in REFLECTOR_SYSTEM.lower()
    assert "stance" in REFLECTOR_SYSTEM.lower()
    r = Reflector()
    ctx = {"date": "2026-06-01", "strategy": "iron_condor", "dte_bucket": "0DTE",
           "trades": [{"trade_id": "A", "book": "learning", "outcome": "open"}],
           "accuracy": {"iron_condor:0DTE:learning": {"n": 1}}}
    p = r._build_substrategy_prompt(ctx)
    assert "iron_condor" in p and "0DTE" in p
    assert "disprove" in p.lower() or "challenge" in p.lower()  # disconfirmation framing
