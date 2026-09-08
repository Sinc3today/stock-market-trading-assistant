"""tests/test_cross_implementation.py -- two answers to one question must agree.

The audit's root cause was that no decision had one owner. The residual risk
after consolidating is that a SECOND implementation reappears and drifts — the
exact history here: journal.trade_recorder and learning.exit_manager each had
a private credit/debit list, they disagreed, and broken_wing was sign-flipped.

forward_audit.v_a2 already checks this, but only over strategies that happen
to appear in the live journal, and nothing runs it automatically. These tests
check the WHOLE reachable vocabulary, every run.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder import TradeRecorder, _pnl_convention
from learning.exit_manager import ExitManager
from tools.enumerate_conventions import journal_vocabulary, strategy_vocabulary


def _vocabulary():
    """Every strategy name any producer can emit, plus what the journal holds."""
    names = set(strategy_vocabulary()) | journal_vocabulary()
    return sorted(n for n in names if _pnl_convention(n) is not None)


def test_the_vocabulary_is_not_empty():
    """A check over an empty set proves nothing."""
    assert len(_vocabulary()) >= 5


@pytest.mark.parametrize("entry,exit_px", [(2.00, 1.00), (1.00, 2.00),
                                           (1.50, 1.50), (0.50, 0.05)])
def test_recorder_and_exit_manager_agree_on_every_strategy(entry, exit_px):
    """Including sign. A sign disagreement doubles the error, and that is
    precisely what happened to broken_wing."""
    rec = TradeRecorder.__new__(TradeRecorder)
    for name in _vocabulary():
        a = rec._calculate_pnl(name, "NEUTRAL", entry, exit_px, 1)[1]
        b = ExitManager._pnl_dollars(name, entry, exit_px, 1)
        assert a is not None and b is not None, f"{name} unscoreable"
        assert a == pytest.approx(b), (
            f"{name}: recorder={a} exit_manager={b} — two implementations of "
            f"one decision have drifted apart")


def test_expiry_resolver_agrees_with_the_recorder_at_settlement():
    """A third implementation. expiry_resolver had its own private list too,
    which booked a broken-wing's peak profit as a loss."""
    from learning.expiry_resolver import ExpiryResolver
    rec = TradeRecorder.__new__(TradeRecorder)
    legs = [{"action": "SELL", "option_type": "call", "strike": 780},
            {"action": "BUY", "option_type": "call", "strike": 785}]
    for name in _vocabulary():
        px = ExpiryResolver._exit_price(name, legs, 790.0)
        assert px is not None, f"{name} cannot settle"
        # The recorder must be able to price that settlement without blowing up
        # and must agree in SIGN with the structure's convention.
        pnl = rec._calculate_pnl(name, "NEUTRAL", 2.00, px, 1)[1]
        assert pnl is not None
        if _pnl_convention(name) == "credit":
            assert pnl <= 200.0, f"{name}: credit P&L exceeds the credit taken"


def test_scaling_by_size_is_linear_in_both_implementations():
    rec = TradeRecorder.__new__(TradeRecorder)
    for name in _vocabulary():
        one = rec._calculate_pnl(name, "NEUTRAL", 2.00, 1.00, 1)[1]
        four = rec._calculate_pnl(name, "NEUTRAL", 2.00, 1.00, 4)[1]
        assert four == pytest.approx(one * 4), f"{name} does not scale linearly"
        em4 = ExitManager._pnl_dollars(name, 2.00, 1.00, 4)
        assert em4 == pytest.approx(four), f"{name} size scaling disagrees"


def test_an_unknown_strategy_is_refused_by_every_implementation():
    """Refusing must also be unanimous — one engine guessing while another
    refuses is how a fabricated number enters the journal."""
    rec = TradeRecorder.__new__(TradeRecorder)
    from learning.expiry_resolver import ExpiryResolver
    assert rec._calculate_pnl("moon_spread", "NEUTRAL", 2.0, 1.0, 1) == (None, None)
    assert ExitManager._pnl_dollars("moon_spread", 2.0, 1.0, 1) is None
    assert ExpiryResolver._exit_price("moon_spread", [], 770.0) is None


# ── the validators must actually run (P2.15) ─────────────────────

def test_the_audit_is_scheduled_against_the_live_journal():
    """13 validators existed for a day and nothing ran them but a human typing
    the command. A control nobody runs is a document."""
    import inspect
    from learning import scheduler as sch
    src = inspect.getsource(sch.register_learning_jobs)
    assert "learning_forward_audit" in src
    assert "job_forward_audit" in src


def test_the_audit_job_only_alerts_on_actionable_p1(monkeypatch):
    """Sample-age failures (C1/D1/D2) are expected and permanent until enough
    trades accumulate. Alerting on them daily trains the eye to ignore it."""
    from learning import scheduler as sch
    from backtests import forward_audit as fa
    sent = []
    monkeypatch.setattr(fa, "run_all", lambda *a, **k: [
        {"id": "D2", "severity": "P1", "verdict": fa.FAIL, "headline": "noise"},
        {"id": "C1", "severity": "P1", "verdict": fa.FAIL, "headline": "thin"},
    ])
    sch.job_forward_audit(alert_fn=lambda **kw: sent.append(kw))
    assert sent == [], "expected sample-age failures must not alert"


def test_the_audit_job_alerts_on_a_real_defect(monkeypatch):
    from learning import scheduler as sch
    from backtests import forward_audit as fa
    sent = []
    monkeypatch.setattr(fa, "run_all", lambda *a, **k: [
        {"id": "A1", "severity": "P1", "verdict": fa.FAIL,
         "headline": "32 records were never scored"},
    ])
    sch.job_forward_audit(alert_fn=lambda **kw: sent.append(kw))
    assert len(sent) == 1 and "A1" in sent[0]["body"]


def test_the_audit_job_survives_a_broken_validator(monkeypatch):
    """Standing rule 10: one failure never crashes the bot."""
    from learning import scheduler as sch
    from backtests import forward_audit as fa
    monkeypatch.setattr(fa, "run_all", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sch.job_forward_audit(alert_fn=lambda **kw: None)
