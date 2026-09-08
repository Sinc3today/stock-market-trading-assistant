"""tests/test_round_trip.py -- one trade, all the way through.

Every one of the eight defects found on 2026-09-07 lived at a SEAM: between
two implementations, between a producer's vocabulary and a consumer's, between
a field's declared units and its readers. The suite had 1,600+ tests and
caught none of them, because each test exercised one function with its
neighbours mocked out.

This walks a single trade the whole way — open -> mark -> close -> journal ->
summary -> scorecard — with nothing stubbed, and asserts the numbers survive
the trip intact.
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder import TradeRecorder, round_trip_commission
from learning import forward_scorecard as fs


@pytest.fixture(autouse=True)
def _window_open(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


def _condor_legs(exp):
    return [{"action": "SELL", "option_type": "call", "strike": 805, "expiry": exp},
            {"action": "BUY", "option_type": "call", "strike": 810, "expiry": exp},
            {"action": "SELL", "option_type": "put", "strike": 738, "expiry": exp},
            {"action": "BUY", "option_type": "put", "strike": 733, "expiry": exp}]


@pytest.fixture
def closed_condor():
    """A full lifecycle: open a 1.60-credit condor, close it at 0.40."""
    rec = TradeRecorder()
    exp = (date.today() + timedelta(days=45)).isoformat()
    tid = rec.log_entry("SPY", 1.60, 1, strategy="iron_condor",
                        trade_type="iron_condor", legs=_condor_legs(exp),
                        max_profit=160.0, max_loss=340.0,
                        dte_bucket="45DTE", book="disciplined")
    rec.log_exit(tid, 0.40)
    return rec, tid


# ── the journal ──────────────────────────────────────────────────

def test_pnl_survives_the_round_trip(closed_condor):
    rec, tid = closed_condor
    t = rec.get_trade_by_id(tid)
    assert t["pnl_dollars"] == pytest.approx(120.0)   # (1.60 - 0.40) * 100
    assert t["outcome"] == "win"


def test_units_are_consistent_end_to_end(closed_condor):
    """entry_price is per-share; entry_value is dollars. Mixing them is the
    A3 defect that put 51 records off by 100x."""
    rec, tid = closed_condor
    t = rec.get_trade_by_id(tid)
    assert t["entry_price"] == pytest.approx(1.60)
    assert abs(t["entry_value"]) == pytest.approx(160.0)


def test_gross_minus_commission_equals_net(closed_condor):
    """Conservation. Nothing asserted this before commissions were modelled."""
    rec, tid = closed_condor
    t = rec.get_trade_by_id(tid)
    fee = round_trip_commission(t["strategy"], t["legs"], t["size"])
    assert fee > 0, "an options trade is never free"
    assert t["pnl_net"] == pytest.approx(t["pnl_dollars"] - fee)
    assert t["commission"] == pytest.approx(fee)


def test_pnl_pct_is_consistent_with_pnl_and_cost_basis(closed_condor):
    """_get_cost_basis was the third sibling missed by the A1/A3 fixes, and it
    made pnl_pct 100x too large for variant strategy names."""
    rec, tid = closed_condor
    t = rec.get_trade_by_id(tid)
    basis = rec._get_cost_basis(t["strategy"], t["entry_price"], t["size"],
                                t["max_loss"])
    assert t["pnl_pct"] == pytest.approx(t["pnl_dollars"] / abs(basis) * 100, rel=1e-3)
    assert abs(t["pnl_pct"]) < 1000, "a plausible percentage, not a 100x artifact"


# ── downstream consumers ─────────────────────────────────────────

def test_the_trade_reaches_the_scorecard_exactly_once(closed_condor):
    rec, tid = closed_condor
    trades = rec.get_all_trades()
    assert sum(1 for t in trades if t["trade_id"] == tid) == 1
    book = fs.book_stats(trades)["disciplined"]
    assert book["n"] == 1 and book["wins"] == 1
    assert book["total"] == pytest.approx(120.0)


def test_the_scorecard_reports_it_net_of_fees(closed_condor):
    rec, _ = closed_condor
    book = fs.book_stats(rec.get_all_trades())["disciplined"]
    assert book["net_total"] < book["total"], "net must be below gross"
    assert book["fees"] > 0


def test_integrity_classifies_it_as_scored(closed_condor):
    rec, tid = closed_condor
    assert fs.integrity(rec.get_trade_by_id(tid)) == fs.SCORED


def test_performance_summary_reads_the_trade(closed_condor):
    """journal/performance.py has zero tests of its own and is the sole
    consumer of pnl_pct."""
    rec, _ = closed_condor
    stats = rec.get_summary_stats()
    assert stats["closed"] >= 1
    assert stats["total_pnl"] == pytest.approx(120.0)


def test_the_audit_finds_nothing_wrong_with_a_clean_trade(closed_condor):
    """The validators must not cry wolf on a correct record."""
    from backtests import forward_audit as fa
    rec, _ = closed_condor
    trades = rec.get_all_trades()
    for check in (fa.v_a1_unscored, fa.v_a3_entry_value_scaling,
                  fa.v_b1_impossible_fills, fa.v_c2_voids, fa.v_c4_duplicates):
        assert check(trades)["verdict"] in (fa.PASS, fa.WARN), \
            f"{check.__name__} flagged a clean trade"


# ── an unscoreable trade must stay visibly unscoreable ───────────

def test_an_unscoreable_trade_is_excluded_rather_than_counted_as_a_loss():
    """The A1 remediation's second half: stopping the fabricated $0 was right,
    but unscored records then sat in win-rate denominators as non-wins."""
    rec = TradeRecorder()
    exp = (date.today() + timedelta(days=45)).isoformat()
    good = rec.log_entry("SPY", 1.60, 1, strategy="iron_condor",
                         legs=_condor_legs(exp), max_profit=160.0,
                         max_loss=340.0, book="disciplined")
    rec.log_exit(good, 0.40)
    bad = rec.log_entry("SPY", 1.60, 1, strategy="iron_condor",
                        legs=_condor_legs(exp), max_profit=160.0,
                        max_loss=340.0, book="disciplined")
    rec.mark_unscored(bad, reason="no quotes")

    book = fs.book_stats(rec.get_all_trades())["disciplined"]
    assert book["n"] == 1, "the unscoreable trade must not be counted"
    assert book["win_pct"] == 100.0, "and must not dilute the win rate"
    assert book["excluded"] == 1, "but must be visibly excluded, not hidden"
