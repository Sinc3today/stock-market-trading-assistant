"""tests/test_journal_repair.py -- repairing the records the old engine broke.

Two populations, deliberately treated differently:

  * RESCORE      real entry/exit prices, but P&L was fabricated as $0 because
                 the strategy name matched no branch. Recoverable arithmetic.
  * MARK UNSCORED the 0DTE records whose recorded exit price is fiction (the
                 intrinsic-marking bug). Their P&L is NOT recoverable — the
                 honest repair is to say so, not to invent a number.

The repair must be idempotent and must never touch an open or already-scored
trade.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import journal_repair as jr


def _t(**kw):
    base = {"trade_id": "T1", "ticker": "SPY", "strategy": "iron_condor",
            "book": "disciplined", "outcome": "win", "entry_price": 2.00,
            "exit_price": 1.00, "size": 1, "pnl_dollars": 100.0,
            "notes_entry": "", "notes_exit": "", "dte_bucket": "45DTE",
            "entry_date": "2026-09-01 09:45 AM EST"}
    base.update(kw)
    return base


def _legacy_zero(**kw):
    """A record the old engine zeroed: real prices, fabricated $0 P&L."""
    base = dict(strategy="put_debit_spread", entry_price=0.73, exit_price=1.89,
                pnl_dollars=0, outcome="breakeven",
                notes_exit="[AUTO-EXIT 2026-07-02] time stop")
    base.update(kw)
    return _t(**base)


def _phantom_fill(**kw):
    """A 0DTE record whose exit price is fiction (intrinsic-marking bug)."""
    base = dict(strategy="call_debit_spread", entry_price=0.78, exit_price=0.0,
                pnl_dollars=0, outcome="breakeven", dte_bucket="0DTE",
                notes_exit="[AUTO-EXIT 2026-06-02] stop 75% of max loss fill=$0.00")
    base.update(kw)
    return _t(**base)


# ── planning ─────────────────────────────────────────────────────

def test_legacy_zero_is_planned_for_rescore():
    plan = jr.plan_repairs([_legacy_zero()])
    assert len(plan) == 1
    assert plan[0]["action"] == jr.RESCORE
    assert plan[0]["after"]["pnl_dollars"] == pytest.approx(116.0)
    assert plan[0]["after"]["outcome"] == "win"


def test_rescore_computes_a_loss_correctly():
    plan = jr.plan_repairs([_legacy_zero(entry_price=2.70, exit_price=1.00)])
    assert plan[0]["after"]["pnl_dollars"] == pytest.approx(-170.0)
    assert plan[0]["after"]["outcome"] == "loss"


def test_phantom_fill_is_marked_unscored_not_rescored():
    """Its exit price is fiction — inventing a P&L would launder the bug."""
    plan = jr.plan_repairs([_phantom_fill()])
    assert plan[0]["action"] == jr.MARK_UNSCORED
    assert plan[0]["after"]["pnl_dollars"] is None
    assert plan[0]["after"]["outcome"] == "unscored"


def test_healthy_trade_is_left_alone():
    assert jr.plan_repairs([_t()]) == []


def test_open_trade_is_never_touched():
    assert jr.plan_repairs([_t(outcome="open", pnl_dollars=None)]) == []


def test_void_trade_is_never_touched():
    assert jr.plan_repairs([_t(outcome="void", pnl_dollars=None)]) == []


def test_genuine_breakeven_is_left_alone():
    """entry == exit really is $0 — don't rewrite an honest scratch."""
    t = _t(entry_price=1.50, exit_price=1.50, pnl_dollars=0.0,
           outcome="breakeven")
    assert jr.plan_repairs([t]) == []


def test_record_with_no_exit_price_cannot_be_rescored():
    plan = jr.plan_repairs([_legacy_zero(exit_price=None)])
    assert plan[0]["action"] == jr.MARK_UNSCORED


# ── applying ─────────────────────────────────────────────────────

def test_apply_rewrites_pnl_and_outcome():
    trades = [_legacy_zero()]
    out = jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert out[0]["pnl_dollars"] == pytest.approx(116.0)
    assert out[0]["outcome"] == "win"


def test_apply_annotates_the_repair_in_the_notes():
    trades = [_legacy_zero()]
    out = jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert jr.REPAIR_TAG in out[0]["notes_exit"]
    assert "[AUTO-EXIT" in out[0]["notes_exit"], "original note must survive"


def test_apply_is_idempotent():
    trades = [_legacy_zero()]
    once = jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert jr.plan_repairs(once) == []
    twice = jr.apply_repairs(once, jr.plan_repairs(once))
    assert twice[0]["pnl_dollars"] == pytest.approx(116.0)
    assert twice[0]["notes_exit"].count(jr.REPAIR_TAG) == 1


def test_apply_does_not_mutate_the_input():
    trades = [_legacy_zero()]
    jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert trades[0]["pnl_dollars"] == 0


def test_apply_preserves_unrelated_records():
    trades = [_t(trade_id="keep"), _legacy_zero(trade_id="fix")]
    out = jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert len(out) == 2
    keep = [t for t in out if t["trade_id"] == "keep"][0]
    assert keep["pnl_dollars"] == 100.0 and keep["outcome"] == "win"


# ── the repaired journal must satisfy the auditor ────────────────

def test_repaired_records_are_no_longer_unscored_by_the_scorecard():
    from learning import forward_scorecard as fs
    trades = [_legacy_zero()]
    out = jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert fs.integrity(out[0]) == fs.SCORED


def test_phantom_records_stay_excluded_after_repair():
    from learning import forward_scorecard as fs
    trades = [_phantom_fill()]
    out = jr.apply_repairs(trades, jr.plan_repairs(trades))
    assert fs.integrity(out[0]) == fs.UNSCORED


# ── file safety ──────────────────────────────────────────────────

def test_backup_is_written_before_any_change(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    path = tmp_path / "trades.json"
    original = [_legacy_zero()]
    path.write_text(json.dumps(original))

    jr.run(apply=True)

    backups = list(tmp_path.glob("trades.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())[0]["pnl_dollars"] == 0
    assert json.loads(path.read_text())[0]["pnl_dollars"] == pytest.approx(116.0)


def test_dry_run_changes_nothing_on_disk(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    path = tmp_path / "trades.json"
    path.write_text(json.dumps([_legacy_zero()]))

    plan = jr.run(apply=False)

    assert plan, "dry run should still report what it would do"
    assert json.loads(path.read_text())[0]["pnl_dollars"] == 0
    assert not list(tmp_path.glob("trades.json.bak-*"))
