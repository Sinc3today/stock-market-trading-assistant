"""tests/test_forward_audit.py -- the falsification validators.

A validator that cannot fail is not a validator, so each test here proves the
FAIL path fires on a rigged input and the PASS path fires on a clean one.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests import forward_audit as fa


def _t(**kw):
    base = {"trade_id": "T1", "ticker": "SPY", "strategy": "iron_condor",
            "book": "disciplined", "outcome": "win", "entry_price": 2.00,
            "exit_price": 1.00, "entry_value": -200.0, "size": 1,
            "pnl_dollars": 100.0, "notes_entry": "", "notes_exit": "",
            "dte_bucket": "45DTE", "entry_date": "2026-09-01 09:45 AM EST",
            "legs": [{"strike": 780, "option_type": "call", "action": "SELL"}]}
    base.update(kw)
    return base


# ── Wilson interval ──────────────────────────────────────────────

def test_wilson_small_sample_is_wide():
    lo, hi = fa.wilson(8, 13)
    assert lo < 50 < hi          # 8/13 must not exclude a coin flip
    assert 30 < lo < 40 and 78 < hi < 88


def test_wilson_large_sample_tightens():
    lo, hi = fa.wilson(700, 1000)
    assert lo > 65 and hi < 75


def test_wilson_zero_sample_is_safe():
    assert fa.wilson(0, 0) == (0.0, 0.0)


# ── A1 unscored ──────────────────────────────────────────────────

def test_a1_fails_on_unscored_records():
    r = fa.v_a1_unscored([_t(strategy="put_debit_spread", outcome="breakeven",
                             pnl_dollars=0)])
    assert r["verdict"] == fa.FAIL


def test_a1_passes_when_all_scored():
    assert fa.v_a1_unscored([_t()])["verdict"] == fa.PASS


# ── A2 sign conventions ──────────────────────────────────────────

def test_a2_detects_the_broken_wing_sign_flip():
    """The recorder treats BWB as a credit structure; exit_manager as a debit.
    17 open BWBs currently ride on that disagreement."""
    r = fa.v_a2_sign_conventions([_t(strategy="broken_wing")])
    assert r["verdict"] == fa.FAIL
    assert any("SIGN FLIP" in e for e in r["evidence"])


def test_a2_passes_for_agreeing_strategies():
    r = fa.v_a2_sign_conventions([_t(strategy="iron_condor")])
    assert r["verdict"] == fa.PASS


# ── A3 scaling ───────────────────────────────────────────────────

def test_a3_fails_when_entry_value_is_per_share():
    r = fa.v_a3_entry_value_scaling([_t(entry_price=0.78, entry_value=0.78)])
    assert r["verdict"] == fa.FAIL


def test_a3_passes_on_dollar_scaled_value():
    assert fa.v_a3_entry_value_scaling([_t()])["verdict"] == fa.PASS


# ── A4 commissions ───────────────────────────────────────────────

def test_a4_flags_a_bucket_that_fees_break():
    """Avg just over the $20 bar must fail once 4 legs of commission land."""
    trades = [_t(trade_id=str(i), book="candidate", dte_bucket="7DTE",
                 pnl_dollars=22.0, outcome="win",
                 legs=[{"strike": k, "option_type": "call", "action": "SELL"}
                       for k in (770, 775, 780, 785)])
              for i in range(12)]
    r = fa.v_a4_commissions(trades)
    assert r["verdict"] == fa.FAIL
    assert any("FAILS the $20 avg bar" in e for e in r["evidence"])


# ── B1 impossible fills ──────────────────────────────────────────

def test_b1_fails_on_zero_stop_fill():
    r = fa.v_b1_impossible_fills([_t(exit_price=0.0,
                                     notes_exit="[AUTO-EXIT] stop 75% of max loss fill=$0.00")])
    assert r["verdict"] == fa.FAIL


def test_b1_allows_a_genuine_expiry_at_zero():
    r = fa.v_b1_impossible_fills([_t(exit_price=0.0,
                                     notes_exit="[AUTO-EXIT] expired worthless")])
    assert r["verdict"] == fa.PASS


# ── C1 survivorship ──────────────────────────────────────────────

def test_c1_fails_on_a_claim_from_one_closed_trade():
    """BWB's '100% win' on n=1 with 17 open is not a result."""
    trades = [_t(trade_id="c", book="candidate", dte_bucket="BWB-30DTE",
                 strategy="broken_wing", pnl_dollars=51.0)]
    trades += [_t(trade_id=f"o{i}", book="candidate", dte_bucket="BWB-30DTE",
                  strategy="broken_wing", outcome="open") for i in range(8)]
    r = fa.v_c1_survivorship(trades)
    assert r["verdict"] == fa.FAIL
    assert "BWB" in r["headline"] or "Broken-wing" in r["headline"]


# ── C2 voids ─────────────────────────────────────────────────────

def test_c2_accepts_structural_voids_despite_long_notes():
    """Regression: the reason was matched against a 70-char display slice, so a
    legitimate 'rh-sync reconcile thrash' note was flagged when truncated."""
    note = ("[VOID 2026-07-20 03:01 PM EST] phantom exit from rh-sync reconcile "
            "thrash 2026-07-20; not a real position")
    r = fa.v_c2_voids([_t(outcome="void", pnl_dollars=None, notes_exit=note)])
    assert r["verdict"] == fa.PASS


def test_c2_flags_a_void_that_carried_a_loss():
    r = fa.v_c2_voids([_t(outcome="void", pnl_dollars=-250.0,
                          notes_exit="[VOID] synthetic stub")])
    assert r["verdict"] == fa.FAIL


def test_c2_flags_a_void_with_no_structural_reason():
    r = fa.v_c2_voids([_t(outcome="void", pnl_dollars=None,
                          notes_exit="[VOID] didn't like it")])
    assert r["verdict"] == fa.FAIL


# ── C4 duplicates ────────────────────────────────────────────────

def test_c4_warns_on_identical_entry_signatures():
    dup = _t(trade_id="A"), _t(trade_id="B")
    assert fa.v_c4_duplicates(list(dup))["verdict"] == fa.WARN


def test_c4_passes_on_distinct_entries():
    trades = [_t(trade_id="A"), _t(trade_id="B", entry_price=1.11)]
    assert fa.v_c4_duplicates(trades)["verdict"] == fa.PASS


# ── D2 confidence ────────────────────────────────────────────────

def test_d2_fails_when_win_rate_cannot_beat_chance():
    trades = [_t(trade_id=str(i), pnl_dollars=(10.0 if i < 8 else -10.0),
                 outcome=("win" if i < 8 else "loss")) for i in range(13)]
    r = fa.v_d2_confidence(trades)
    assert r["verdict"] == fa.FAIL
    assert any("coin flip" in e for e in r["evidence"])


def test_d2_passes_with_a_decisive_sample():
    trades = [_t(trade_id=str(i), pnl_dollars=(10.0 if i < 45 else -10.0),
                 outcome=("win" if i < 45 else "loss")) for i in range(50)]
    assert fa.v_d2_confidence(trades)["verdict"] == fa.PASS


# ── runner ───────────────────────────────────────────────────────

def test_run_all_returns_a_verdict_per_validator():
    results = fa.run_all([_t()])
    assert len(results) == len(fa.VALIDATORS) + 1     # +1 for F1
    assert all(r["verdict"] in (fa.PASS, fa.FAIL, fa.WARN, fa.INFO) for r in results)


def test_a_raising_validator_does_not_hide_the_others():
    results = fa.run_all([{"trade_id": "junk"}])
    assert len(results) == len(fa.VALIDATORS) + 1


def test_every_validator_declares_a_severity():
    for r in fa.run_all([_t()]):
        assert r["severity"] in ("P1", "P2", "P3", "?")
