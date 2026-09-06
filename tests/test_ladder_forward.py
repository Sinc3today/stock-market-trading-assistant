"""tests/test_ladder_forward.py -- the 14DTE and 21DTE condor rungs.

docs/DTE_LADDER_EXIT_STUDY.md found these two rungs test better than anything
we run, and that they are unbuilt. They open here as PAPER candidates with
bars pre-registered before a single trade exists.

The exits are DERIVED per rung (14 -> close at 1, 21 -> close at 2), not
scaled from the 45DTE rule. Scaling a time rule across rungs is the defect
that cost the 7DTE book ~$28/trade, and a test below pins that these never
equal the scaled values.
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import ladder_forward as lf


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


# ── rung definitions ─────────────────────────────────────────────

def test_both_rungs_are_defined():
    assert set(lf.RUNGS) == {14, 21}


def test_exits_are_derived_not_scaled():
    """round(dte * 21/45) would give 7 and 10. Those are the wrong answers —
    the scaling defect this project already paid for once."""
    assert lf.RUNGS[14]["close_dte"] == 1
    assert lf.RUNGS[21]["close_dte"] == 2
    assert lf.RUNGS[14]["close_dte"] != round(14 * 21 / 45)
    assert lf.RUNGS[21]["close_dte"] != round(21 * 21 / 45)


def test_every_rung_keeps_an_assignment_buffer():
    """Never hold into expiry day: an ITM short can be assigned."""
    for dte, cfg in lf.RUNGS.items():
        assert cfg["close_dte"] >= 1


def test_buckets_are_distinct_and_labelled_by_rung():
    buckets = {cfg["bucket"] for cfg in lf.RUNGS.values()}
    assert buckets == {"14DTE", "21DTE"}


def test_promotion_bar_is_pre_registered():
    assert "15" in lf.PROMOTION_BAR and "70" in lf.PROMOTION_BAR


# ── opening ──────────────────────────────────────────────────────

def test_opens_one_candidate_per_rung(recorder):
    out = lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0,
                               today=date(2026, 9, 8))
    assert len(out) == 2
    buckets = {t["dte_bucket"] for t in recorder.get_open_trades()}
    assert buckets == {"14DTE", "21DTE"}


def test_opening_is_idempotent_per_day(recorder):
    """log_entry stamps the REAL clock, so idempotency must be exercised with
    the real date — that is the only path production ever takes."""
    today = lf._today_et()
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=today)
    again = lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=today)
    assert again == []
    assert len(recorder.get_open_trades()) == 2


def test_a_new_day_opens_again(recorder, monkeypatch):
    today = lf._today_et()
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=today)
    # Simulate tomorrow by moving the clock the module reads.
    monkeypatch.setattr(lf, "_today_et", lambda: today + timedelta(days=1))
    import journal.trade_recorder as tr
    real_dt = tr.datetime

    class _Clock:
        @staticmethod
        def now(tz=None):
            return real_dt.now(tz) + timedelta(days=1)
    monkeypatch.setattr(tr, "datetime", _Clock)
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0,
                         today=today + timedelta(days=1))
    assert len(recorder.get_open_trades()) == 4


def test_candidates_land_in_the_candidate_book(recorder):
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=date(2026, 9, 8))
    assert all(t["book"] == "candidate" for t in recorder.get_open_trades())


def test_candidates_are_one_lot(recorder):
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=date(2026, 9, 8))
    assert all(t["size"] == 1 for t in recorder.get_open_trades())


def test_notes_name_the_promotion_bar(recorder):
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=date(2026, 9, 8))
    assert all("promotion bar" in (t.get("notes_entry") or "").lower()
               for t in recorder.get_open_trades())


def test_disabled_flag_opens_nothing(recorder, monkeypatch):
    import config
    monkeypatch.setattr(config, "LADDER_FORWARD_ENABLED", False, raising=False)
    assert lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0,
                                today=date(2026, 9, 8)) == []


def test_entry_window_is_respected(recorder, monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", True)
    monkeypatch.setattr(config, "within_entry_window", lambda: False)
    assert lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0,
                                today=date(2026, 9, 8)) == []


# ── resolving ────────────────────────────────────────────────────

def test_closes_at_the_rung_time_stop(recorder):
    open_day = date(2026, 9, 8)
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=open_day)
    # 13 days on: the 14DTE rung has 1 DTE left -> its time stop fires.
    closed = lf.resolve_ladder(recorder, spy_spot=700.0, vix=15.0,
                               today=open_day + timedelta(days=13))
    buckets = {t["dte_bucket"] for t in closed}
    assert "14DTE" in buckets


def test_does_not_close_early(recorder):
    open_day = date(2026, 9, 8)
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=open_day)
    closed = lf.resolve_ladder(recorder, spy_spot=700.0, vix=15.0,
                               today=open_day + timedelta(days=1))
    assert [t for t in closed if t["dte_bucket"] == "14DTE"] == []


def test_profit_target_closes_before_the_time_stop(recorder):
    """A big favourable move should trigger the 70% target early."""
    open_day = date(2026, 9, 8)
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=open_day)
    # Vol collapse -> the condor is worth almost nothing to buy back.
    closed = lf.resolve_ladder(recorder, spy_spot=700.0, vix=3.0,
                               today=open_day + timedelta(days=3))
    assert closed, "70% of max profit should have been reached"
    assert all(t.get("exit_reason") == "target" for t in closed)


def test_resolver_ignores_other_buckets(recorder):
    from learning.seven_dte_forward import maybe_open_seven_dte
    maybe_open_seven_dte(recorder, spy_spot=700.0, vix=15.0, today=date(2026, 9, 8))
    closed = lf.resolve_ladder(recorder, spy_spot=700.0, vix=15.0,
                               today=date(2026, 9, 30))
    assert all(t["dte_bucket"] in ("14DTE", "21DTE") for t in closed)


def test_resolver_is_safe_with_nothing_open(recorder):
    assert lf.resolve_ladder(recorder, spy_spot=700.0, vix=15.0) == []


# ── paper record ─────────────────────────────────────────────────

def test_paper_record_is_per_rung(recorder):
    rec = lf.paper_record(recorder)
    assert set(rec) == {14, 21}
    assert rec[14]["n"] == 0 and not rec[14]["meets_bar"]


def test_paper_record_counts_closed_trades(recorder):
    open_day = date(2026, 9, 8)
    lf.maybe_open_ladder(recorder, spy_spot=700.0, vix=15.0, today=open_day)
    lf.resolve_ladder(recorder, spy_spot=700.0, vix=3.0,
                      today=open_day + timedelta(days=3))
    rec = lf.paper_record(recorder)
    assert rec[14]["n"] == 1 and rec[21]["n"] == 1


def test_bar_needs_all_three_conditions(recorder):
    """15 closed, >=70% win, avg > $20 — any one failing blocks promotion."""
    assert not lf._meets_bar(n=14, wins=14, avg=100.0)
    assert not lf._meets_bar(n=15, wins=9, avg=100.0)     # 60% win
    assert not lf._meets_bar(n=15, wins=15, avg=5.0)      # thin average
    assert lf._meets_bar(n=15, wins=11, avg=25.0)


# ── scheduler wiring ─────────────────────────────────────────────

def test_resolver_is_wired_into_the_daily_job():
    """Piggybacks the existing 16:12 candidate-resolver slot rather than adding
    another cron; wrapped separately per Standing Rule #10."""
    import inspect
    from learning import scheduler as sch
    src = inspect.getsource(sch.job_dipbuy_resolver)
    assert "resolve_ladder" in src
    assert "ladder_forward resolver failed" in src, "must be its own try/except"


def test_opener_is_wired_into_the_entry_run():
    import inspect
    from scheduler import spy_daily_scheduler as sds
    assert "_run_ladder_forward" in inspect.getsource(sds)
    assert "maybe_open_ladder" in inspect.getsource(sds._run_ladder_forward)


def test_opener_only_fires_on_condor_regime_days():
    import inspect
    src = inspect.getsource(
        __import__("scheduler.spy_daily_scheduler", fromlist=["x"])._run_ladder_forward)
    assert 'strategy") != "iron_condor"' in src


def test_promotion_bars_are_on_the_dashboard():
    from learning.forward_scorecard import PROMOTION_BARS
    assert "14DTE" in PROMOTION_BARS and "21DTE" in PROMOTION_BARS
