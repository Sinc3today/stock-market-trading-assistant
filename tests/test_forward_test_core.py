"""tests/test_forward_test_core.py -- one implementation of the forward test.

Five generators (seven_dte, qqq_condor, broken_wing, ladder, dipbuy) shared
75-86% of their bodies. Every defect found on 2026-09-07 existed in some but
not all of them:

  * the book filter — three had it, qqq_condor did NOT, so on promotion both
    its resolver and ExitManager would manage the position and both log_exit
  * the risk guards — paper_broker enforces four, ALL FIVE generators enforce
    none, which is how 26 correlated short-vol positions accumulated under a
    concentration guard that is switched on
  * net-vs-gross promotion scoring — ladder used net, seven_dte and bwb used
    gross, against a bar defined as "net of commissions"
  * RULE_EPOCH — only seven_dte had one

Consolidating means the next fix lands everywhere by construction, which is
the whole point: the root cause was never a bug, it was that no decision had
one owner.
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.forward_test import ForwardSpec, ForwardTest


@pytest.fixture(autouse=True)
def _window_open(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    return TradeRecorder()


def _spec(**kw):
    base = dict(name="test", ticker="SPY", buckets={"TEST-7": 1},
                promotion_bar="n>=15 closed, win>=70%, avg>$20 net of fees")
    base.update(kw)
    return ForwardSpec(**base)


def _condor_legs(exp="2026-10-16"):
    return [{"action": "SELL", "option_type": "call", "strike": 805, "expiry": exp},
            {"action": "BUY", "option_type": "call", "strike": 810, "expiry": exp},
            {"action": "SELL", "option_type": "put", "strike": 738, "expiry": exp},
            {"action": "BUY", "option_type": "put", "strike": 733, "expiry": exp}]


def _open(rec, ft, bucket="TEST-7", exp_days=7, credit=1.60, book="candidate"):
    exp = (date.today() + timedelta(days=exp_days)).isoformat()
    return rec.log_entry("SPY", credit, 1, strategy="iron_condor",
                         trade_type="iron_condor", legs=_condor_legs(exp),
                         max_profit=credit * 100, max_loss=(5 - credit) * 100,
                         dte_bucket=bucket, book=book,
                         notes=f"[CANDIDATE] {ft.spec.name}")


# ── the book filter every sibling should have had ────────────────

def test_resolver_ignores_promoted_positions(recorder):
    """A promoted position is the ExitManager's job. qqq_condor_forward lacked
    this, so both would have managed it and both called log_exit."""
    ft = ForwardTest(_spec())
    _open(recorder, ft, book="disciplined")
    assert ft.resolve(recorder, spot=770.0, vol=15.0) == []
    assert recorder.get_open_trades()[0]["outcome"] == "open"


def test_resolver_ignores_other_buckets(recorder):
    ft = ForwardTest(_spec())
    _open(recorder, ft, bucket="SOMETHING-ELSE")
    assert ft.resolve(recorder, spot=770.0, vol=15.0) == []


def test_resolver_ignores_already_closed_trades(recorder):
    ft = ForwardTest(_spec())
    tid = _open(recorder, ft)
    recorder.log_exit(tid, 0.40)
    assert ft.resolve(recorder, spot=770.0, vol=15.0) == []


# ── exits ────────────────────────────────────────────────────────

def test_time_stop_fires_at_the_bucket_close_dte(recorder):
    # target_pct above 1.0 is unreachable, isolating the time stop.
    ft = ForwardTest(_spec(buckets={"TEST-7": 1}, target_pct=99.0))
    _open(recorder, ft, exp_days=7)
    early = ft.resolve(recorder, spot=770.0, vol=15.0,
                       today=date.today() + timedelta(days=2))
    assert early == []
    late = ft.resolve(recorder, spot=770.0, vol=15.0,
                      today=date.today() + timedelta(days=6))
    assert late and late[0]["exit_reason"] == "time_stop"


def test_profit_target_fires_before_the_time_stop(recorder):
    ft = ForwardTest(_spec())
    _open(recorder, ft)
    out = ft.resolve(recorder, spot=770.0, vol=2.0,          # vol collapse
                     today=date.today() + timedelta(days=1))
    assert out and out[0]["exit_reason"] == "target"


def test_each_bucket_uses_its_own_close_dte(recorder):
    """Per-rung exits, never one scaled from another — that scaling cost the
    7DTE book ~$28/trade."""
    ft = ForwardTest(_spec(buckets={"A": 1, "B": 5}, target_pct=99.0))
    _open(recorder, ft, bucket="A", exp_days=7)
    _open(recorder, ft, bucket="B", exp_days=7)
    out = ft.resolve(recorder, spot=770.0, vol=15.0,
                     today=date.today() + timedelta(days=3))
    assert [c["dte_bucket"] for c in out] == ["B"], "only B's 5-DTE stop is due"


def test_close_cost_is_not_clamped(recorder):
    """A broken-wing is long a far wing, so its cost to close can legitimately
    be negative. Clamping booked phantom max-loss (audit A2)."""
    import inspect
    src = inspect.getsource(ForwardTest.resolve)
    assert "max(0.0," not in src and "max(0," not in src


# ── the risk guards no generator enforced ────────────────────────

def test_open_respects_the_entry_window(recorder, monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", True)
    monkeypatch.setattr(config, "within_entry_window", lambda: False)
    ft = ForwardTest(_spec())
    assert ft.may_open(recorder, "TEST-7") is False


def test_open_is_idempotent_per_bucket_per_day(recorder):
    ft = ForwardTest(_spec())
    _open(recorder, ft)
    assert ft.may_open(recorder, "TEST-7") is False


def test_a_different_bucket_may_still_open_today(recorder):
    ft = ForwardTest(_spec(buckets={"A": 1, "B": 2}))
    _open(recorder, ft, bucket="A")
    assert ft.may_open(recorder, "B") is True


def test_disabled_flag_blocks_opening(recorder, monkeypatch):
    import config
    monkeypatch.setattr(config, "TEST_FORWARD_ENABLED", False, raising=False)
    ft = ForwardTest(_spec(enabled_flag="TEST_FORWARD_ENABLED"))
    assert ft.may_open(recorder, "TEST-7") is False


def test_a_missing_flag_defaults_to_enabled(recorder):
    ft = ForwardTest(_spec(enabled_flag="NO_SUCH_FLAG_ANYWHERE"))
    assert ft.may_open(recorder, "TEST-7") is True


def test_concentration_guard_is_enforced(recorder, monkeypatch):
    """paper_broker enforces this; not one generator did. That is how 26
    correlated short-vol positions accumulated with the guard switched on."""
    import config
    monkeypatch.setattr(config, "ENFORCE_CONCENTRATION_GUARD", True, raising=False)
    monkeypatch.setattr(config, "MAX_CONCURRENT_CANDIDATES", 2, raising=False)
    ft = ForwardTest(_spec(buckets={"A": 1, "B": 2, "C": 3}))
    _open(recorder, ft, bucket="A")
    _open(recorder, ft, bucket="B")
    assert ft.may_open(recorder, "C") is False


# ── promotion record ─────────────────────────────────────────────

def test_paper_record_scores_net_of_fees(recorder):
    """The bar says 'net of commissions'; seven_dte and bwb scored gross."""
    ft = ForwardTest(_spec())
    tid = _open(recorder, ft)
    recorder.log_exit(tid, 0.40)
    rec = ft.paper_record(recorder)["TEST-7"]
    gross = recorder.get_trade_by_id(tid)["pnl_dollars"]
    assert rec["avg"] < gross, "average must be net of commissions"


def test_paper_record_needs_all_three_bar_conditions(recorder):
    ft = ForwardTest(_spec())
    assert not ft._meets_bar(n=14, wins=14, avg=100.0)
    assert not ft._meets_bar(n=15, wins=9, avg=100.0)
    assert not ft._meets_bar(n=15, wins=15, avg=5.0)
    assert ft._meets_bar(n=15, wins=11, avg=25.0)


def test_rule_epoch_separates_a_retired_ruleset(recorder):
    """Pooling across a rule change describes a strategy nobody runs (E2).
    Only seven_dte had this; now every generator does."""
    ft = ForwardTest(_spec(rule_epoch="2099-01-01"))
    tid = _open(recorder, ft)
    recorder.log_exit(tid, 0.40)
    rec = ft.paper_record(recorder)["TEST-7"]
    assert rec["n"] == 0, "pre-epoch trades must not count toward the bar"
    assert rec["legacy"]["n"] == 1


def test_paper_record_is_empty_and_safe_with_no_trades(recorder):
    rec = ForwardTest(_spec()).paper_record(recorder)
    assert rec["TEST-7"]["n"] == 0
    assert not rec["TEST-7"]["meets_bar"]
