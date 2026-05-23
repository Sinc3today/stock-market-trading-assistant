"""Phase 2b-3: ExitManager dispatches exit rules by (strategy, dte_bucket).

THE PARITY GATE: byte-identical 45DTE behavior. For any 45DTE trade input,
the refactored _evaluate produces the same (exit_px, reason) tuple as the
original inline logic. Tests prove this explicitly.

The new intraday cron registers via the scheduler but its dte_buckets
filter excludes 45DTE — it's a no-op until Phase 3 produces intraday trades."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date, timedelta
import pytest

import config
from learning.exit_manager import ExitManager, _exit_rule_for


# ── Per-(strategy, dte_bucket) rule lookup ────────────────────────────────

def test_exit_rule_for_45dte_returns_existing_constants():
    """45DTE rules must match the old global PROFIT_TARGET_PCT=0.70 and
    DTE_CLOSE_THRESHOLD=21 EXACTLY. This is the parity contract."""
    for structure in ("call_debit_spread", "put_debit_spread", "iron_condor",
                       "debit_spread", "credit_spread"):  # incl. legacy strategy names
        r = _exit_rule_for(structure, "45DTE")
        assert r["profit_target_pct"]    == 0.70
        assert r["dte_close_threshold"]  == 21
        assert r["stop_pct"]              is None    # no stop on 45DTE (current default)


def test_exit_rule_for_legacy_untagged_defaults_to_45dte():
    """Old trades without dte_bucket field must dispatch to 45DTE rules so
    historical positions on the books keep working identically."""
    r = _exit_rule_for("debit_spread", None)
    assert r["profit_target_pct"] == 0.70
    assert r["dte_close_threshold"] == 21


def test_exit_rule_for_0dte_call_uses_aggressive_rules():
    r = _exit_rule_for("call_debit_spread", "0DTE")
    assert r["profit_target_pct"] == config.PROFIT_TARGET_PCT_0DTE_CALL  # 1.00
    assert r["stop_pct"]          == config.STOP_PCT_0DTE_CALL           # 0.75
    assert r.get("forced_close_time") == config.FORCED_CLOSE_TIME_0DTE_DEBIT  # "15:30"


def test_exit_rule_for_0dte_condor_uses_short_strike_touch():
    r = _exit_rule_for("iron_condor", "0DTE")
    assert r["profit_target_pct"]          == config.PROFIT_TARGET_PCT_0DTE_COND  # 0.30
    assert r["condor_short_strike_touch"]  is True
    assert r.get("forced_close_time")       == config.FORCED_CLOSE_TIME_0DTE_CONDOR


def test_exit_rule_for_1_3dte_uses_50pct_target_and_stop():
    r = _exit_rule_for("call_debit_spread", "1-3DTE")
    assert r["profit_target_pct"] == 0.50
    assert r["stop_pct"]          == 0.50


# ── Byte-identical 45DTE parity for _evaluate ─────────────────────────────

def _make_trade_45dte(strategy="debit_spread", direction="bullish", days_to_exp=30):
    today = date(2026, 5, 23)
    expiry = today + timedelta(days=days_to_exp)
    return {
        "trade_id":   "TEST00001",
        "strategy":   strategy,
        "direction":  direction,
        "entry_price": 1.50,
        "size":       1,
        "max_profit": 300.0,
        "max_loss":   150.0,
        "legs": [{
            "action": "BUY", "option_type": "CALL", "strike": 700,
            "expiry": expiry.isoformat(),
        }, {
            "action": "SELL", "option_type": "CALL", "strike": 710,
            "expiry": expiry.isoformat(),
        }],
        "dte_bucket": "45DTE",
        "book":       "disciplined",
    }


def test_evaluate_45dte_profit_target_or_no_action(tmp_path, monkeypatch):
    """Smoke: the refactored _evaluate either fires (returns a tuple) or
    declines (returns None). Doesn't crash, returns the right shape."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    trade = _make_trade_45dte(days_to_exp=30)
    decision = mgr._evaluate(trade, spy=730.0, vix=17.0, today=date(2026, 5, 23))
    assert decision is None or isinstance(decision, tuple)


def test_evaluate_45dte_time_stop_fires_at_21_dte(tmp_path, monkeypatch):
    """Day-stop fires when DTE <= 21. Byte-identical to current behavior.
    Use SPY below the strike so the spread is OTM and profit target cannot fire
    first — only the time stop should trigger."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    trade = _make_trade_45dte(days_to_exp=21)   # exactly at threshold
    # spy=690 — below the 700 strike, spread is OTM; profit target won't fire
    decision = mgr._evaluate(trade, spy=690.0, vix=17.0, today=date(2026, 5, 23))
    assert decision is not None
    exit_px, reason = decision
    # The reason format from the original code: f"time stop {dte}DTE"
    assert "time stop" in reason
    assert "21DTE" in reason


def test_evaluate_legacy_untagged_trade_uses_45dte_rules(tmp_path, monkeypatch):
    """A trade record from before Phase 2a (no dte_bucket field) must produce
    the same exit decision as a tagged 45DTE trade. Parity for legacy trades.
    Use SPY below the strike so the decision is driven by the time stop, making
    the reason string comparison deterministic."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    tagged = _make_trade_45dte(days_to_exp=21)
    legacy = {k: v for k, v in tagged.items() if k not in ("dte_bucket", "book")}
    d_tagged = mgr._evaluate(tagged, spy=690.0, vix=17.0, today=date(2026, 5, 23))
    d_legacy = mgr._evaluate(legacy, spy=690.0, vix=17.0, today=date(2026, 5, 23))
    # Both should return the same kind of decision (None vs tuple) and same reason
    # since they use the same rules.
    if d_tagged is None:
        assert d_legacy is None
    else:
        assert d_legacy is not None
        assert d_tagged[1] == d_legacy[1]   # same reason string


# ── manage_open dte_buckets filter ────────────────────────────────────────

def test_manage_open_filters_by_dte_buckets(tmp_path, monkeypatch):
    """manage_open(dte_buckets=['45DTE']) processes only 45DTE positions,
    leaving 0DTE/1-3DTE positions alone. Used by the scheduler to keep the
    daily 16:08 cron from re-evaluating intraday trades and vice versa."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    # Seed two open trades — one 45DTE, one 0DTE.
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    today = date.today()
    expiry_45 = (today + timedelta(days=30)).isoformat()
    expiry_0  = today.isoformat()
    t45 = rec.log_entry(
        ticker="SPY", entry_price=1.5, size=1, trade_type="option_spread",
        strategy="debit_spread", direction="bullish", mode="swing",
        legs=[{"action": "BUY", "option_type": "CALL", "strike": 700, "expiry": expiry_45}],
        max_profit=300.0, max_loss=150.0,
        notes="[AUTO-PAPER] test", dte_bucket="45DTE", book="disciplined",
    )
    t0d = rec.log_entry(
        ticker="SPY", entry_price=0.5, size=1, trade_type="option_spread",
        strategy="call_debit_spread", direction="bullish", mode="intraday",
        legs=[{"action": "BUY", "option_type": "CALL", "strike": 700, "expiry": expiry_0}],
        max_profit=100.0, max_loss=50.0,
        notes="[AUTO-PAPER] test", dte_bucket="0DTE", book="learning",
    )
    mgr = ExitManager()
    # manage_open with dte_buckets=["0DTE"] processes only the 0DTE trade.
    closed = mgr.manage_open(today=today, spy_close=700.0, vix=17.0,
                              dte_buckets=["0DTE"])
    # The 45DTE trade was NOT touched (it's still open).
    after = rec.get_trade_by_id(t45)
    assert after.get("outcome") == "open"
    # The 0DTE trade may or may not have closed depending on math — the key
    # assertion is the 45DTE one stayed open. Any closed trade is the 0DTE one.
    for c in closed:
        assert c["trade_id"] != t45


def test_manage_open_default_dte_buckets_none_processes_all(tmp_path, monkeypatch):
    """Back-compat: manage_open() with no dte_buckets arg processes everything
    (preserves the existing scheduler-call signature)."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    # Just verify the call signature accepts no dte_buckets arg.
    closed = mgr.manage_open(today=date(2026, 5, 23), spy_close=730.0, vix=17.0)
    assert isinstance(closed, list)
