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


def test_reflect_today_runs_once_per_active_substrategy(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    # two active sub-strategies today
    monkeypatch.setattr(r.trades, "get_all_trades", lambda: [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "learning", "2026-06-01 10:00 AM EST"),
    ])
    calls = []
    def fake_reflect_one(prompt, scope, today_str, context):
        calls.append(scope)
        return {"kb_ids": [], "markdown": "x", "route": "phi4", "parsed": True}
    monkeypatch.setattr(r, "_reflect_one", fake_reflect_one)
    out = r.reflect_today(today=__import__("datetime").date(2026, 6, 1))
    scopes = {(s.get("strategy"), s.get("dte_bucket")) for s in calls}
    assert scopes == {("iron_condor", "0DTE"), ("call_debit_spread", "1-3DTE")}
    assert out["units"] == 2


def test_reflect_today_standby_when_no_active(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    monkeypatch.setattr(r.trades, "get_all_trades", lambda: [])  # no trades today
    calls = []
    monkeypatch.setattr(r, "_reflect_one",
                        lambda prompt, scope, today_str, context: calls.append(scope) or
                        {"kb_ids": [], "markdown": "x", "route": "phi4", "parsed": True})
    out = r.reflect_today(today=__import__("datetime").date(2026, 6, 1))
    assert len(calls) == 1
    assert calls[0].get("strategy") is None   # standby unit
    assert out["units"] == 1


def test_reflect_today_one_failure_does_not_sink_others(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    monkeypatch.setattr(r.trades, "get_all_trades", lambda: [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "learning", "2026-06-01 10:00 AM EST"),
    ])
    def flaky(prompt, scope, today_str, context):
        if scope.get("strategy") == "iron_condor":
            raise RuntimeError("LLM boom")
        return {"kb_ids": ["k1"], "markdown": "x", "route": "phi4", "parsed": True}
    monkeypatch.setattr(r, "_reflect_one", flaky)
    out = r.reflect_today(today=__import__("datetime").date(2026, 6, 1))
    assert out["units"] == 2 and out["failed"] == 1 and "k1" in out["kb_ids"]


# ── TDD: ground-truth extraction from scoped trades ───────────────────────


def test_extract_trade_ids_reads_scoped_trades(monkeypatch, tmp_path):
    """Scoped context with trades list → _extract_today_trade_ids returns those IDs.

    Sub-strategy contexts carry 'trades' (not 'open_positions'), so the helper
    must read both to give the kb_validator real ground-truth for sub-strategy units.
    """
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    ctx = {
        "date": "2026-06-01",
        "strategy": "iron_condor",
        "dte_bucket": "0DTE",
        "trades": [
            {"trade_id": "AB12", "entry_price": 1.10, "outcome": "open"},
            {"trade_id": "CD34", "entry_price": 0.95, "outcome": "closed"},
        ],
        "accuracy": {},
    }
    ids = r._extract_today_trade_ids(ctx)
    assert "AB12" in ids, "trade_id from scoped trades must appear in extracted set"
    assert "CD34" in ids, "second trade_id from scoped trades must appear in extracted set"


def test_extract_numbers_reads_scoped_trades(monkeypatch, tmp_path):
    """Scoped context with a trade carrying numeric fields → those floats are in the set.

    Verifies: entry_price, max_loss are extracted (representative sample of the fields
    listed in the fix spec). The set stores raw Python floats matching what
    _extract_today_numbers already uses for open_positions (isinstance int/float → add as-is).
    """
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    ctx = {
        "date": "2026-06-01",
        "strategy": "iron_condor",
        "dte_bucket": "0DTE",
        "trades": [
            {
                "trade_id": "AB12",
                "entry_price": 1.55,
                "max_profit": 200.0,
                "max_loss": 345.0,
                "pnl_dollars": -50.0,
                "pnl_pct": -0.14,
                "legs": [{"strike": 490}, {"strike": 495}],
            }
        ],
        "accuracy": {"iron_condor:0DTE:disciplined": {"accuracy": 0.72}},
    }
    nums = Reflector._extract_today_numbers(ctx)
    assert 1.55 in nums,   "entry_price must be extracted from scoped trades"
    assert 345.0 in nums,  "max_loss must be extracted from scoped trades"
    # Also verify the scoped accuracy dict's numeric value is extracted
    assert 0.72 in nums,   "accuracy value from ctx['accuracy'] must be extracted"
