"""tests/test_seven_dte_forward.py -- 7DTE SPY condor paper candidates.

Best undeployed rung from the DTE-ladder study (82%/$33.50 under a 10% fill
haircut). Runs zero-capital with an explicit promotion bar (n>=15, win>=70%,
avg>$20) before it may follow the 1-3DTE path to live mirroring.
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def _window_open(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def test_open_records_candidate_and_is_idempotent(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.seven_dte_forward import maybe_open_seven_dte, BUCKET
    rec = TradeRecorder()
    r = maybe_open_seven_dte(rec, spy_spot=750.0, vix=16.0)
    assert r and r["recorded"]
    t = rec.get_open_trades()[0]
    assert t["ticker"] == "SPY" and t["strategy"] == "iron_condor"
    assert t["book"] == "candidate" and t["dte_bucket"] == BUCKET
    assert t["entry_price"] > 0 and len(t["legs"]) == 4
    assert "promotion bar" in t["notes_entry"]
    assert maybe_open_seven_dte(rec, spy_spot=750.0, vix=16.0) is None


def test_expiry_is_about_seven_days_out(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.seven_dte_forward import maybe_open_seven_dte
    rec = TradeRecorder()
    maybe_open_seven_dte(rec, spy_spot=750.0, vix=16.0)
    legs = rec.get_open_trades()[0]["legs"]
    exp = date.fromisoformat(legs[0]["expiry"])
    assert 4 <= (exp - date.today()).days <= 13   # nearest Friday to +7d


def test_resolver_closes_at_time_stop(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.seven_dte_forward import (
        CLOSE_DTE, DTE, maybe_open_seven_dte, resolve_seven_dte,
    )
    rec = TradeRecorder()
    maybe_open_seven_dte(rec, spy_spot=750.0, vix=16.0)
    later = date.today() + timedelta(days=DTE + 6 - CLOSE_DTE)
    closed = resolve_seven_dte(rec, spy_spot=750.0, vix=16.0, today=later)
    assert len(closed) == 1
    assert rec.get_all_trades()[0]["outcome"] in ("win", "loss", "breakeven")


def test_resolver_holds_young_position(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.seven_dte_forward import maybe_open_seven_dte, resolve_seven_dte
    rec = TradeRecorder()
    maybe_open_seven_dte(rec, spy_spot=750.0, vix=16.0)
    closed = resolve_seven_dte(rec, spy_spot=750.0, vix=16.0,
                               today=date.today() + timedelta(days=1))
    assert closed == []


def test_resolver_ignores_promoted_disciplined_trades(iso):
    # once 7DTE is promoted, disciplined-book 7DTE trades belong to the exit
    # manager — the candidate resolver must not touch them
    from journal.trade_recorder import TradeRecorder
    from learning.seven_dte_forward import resolve_seven_dte
    rec = TradeRecorder()
    rec.log_entry(ticker="SPY", entry_price=1.2, size=1, trade_type="iron_condor",
                  strategy="iron_condor", direction="neutral", mode="swing",
                  legs=[{"action": "SELL", "option_type": "PUT", "strike": 730,
                         "expiry": date.today().isoformat()}],
                  max_profit=120.0, max_loss=380.0,
                  dte_bucket="7DTE", book="disciplined")
    closed = resolve_seven_dte(rec, spy_spot=750.0, vix=16.0)
    assert closed == []


def test_paper_record_tracks_promotion_bar(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.seven_dte_forward import maybe_open_seven_dte, paper_record, resolve_seven_dte
    rec = TradeRecorder()
    r = paper_record(rec)
    assert r["n"] == 0 and r["meets_bar"] is False
    maybe_open_seven_dte(rec, spy_spot=750.0, vix=16.0)
    resolve_seven_dte(rec, spy_spot=750.0, vix=16.0,
                      today=date.today() + timedelta(days=10))
    r = paper_record(rec)
    assert r["n"] == 1 and "win" in str(r).lower() or r["n"] == 1
    assert r["meets_bar"] is False        # 1 trade is nowhere near n>=15


# ── exit-rule epoch (2026-09-06) ──────────────────────────────────
# CLOSE_DTE was 3, derived as round(7 * 21/45) — the 45DTE time-stop scaled
# proportionally. Theta is not linear in DTE, so that surrendered the most
# valuable days: the sweep showed hold-to-expiry +$32.39 (PASS) vs close-at-3
# +$2.27 (fail-OOS). See docs/SEVEN_DTE_STRUCTURE_STUDY.md.

def test_close_dte_is_no_longer_the_proportional_scaling():
    from learning import seven_dte_forward as sdf
    assert sdf.CLOSE_DTE == 1, "3 was the disproved proportional heuristic"


def test_paper_record_counts_only_the_current_rule(tmp_path, monkeypatch):
    """Pooling across a rule change describes a strategy nobody runs (E2)."""
    from learning import seven_dte_forward as sdf

    class _Rec:
        def get_all_trades(self):
            return [
                {"dte_bucket": "7DTE", "entry_date": "2026-08-01 09:45 AM EST",
                 "pnl_dollars": -300.0},
                {"dte_bucket": "7DTE", "entry_date": "2026-08-02 09:45 AM EST",
                 "pnl_dollars": -200.0},
                {"dte_bucket": "7DTE", "entry_date": "2026-09-10 09:45 AM EST",
                 "pnl_dollars": 40.0},
            ]

    rec = sdf.paper_record(_Rec())
    assert rec["n"] == 1                      # only the post-epoch trade
    assert rec["avg"] == 40.0
    assert rec["legacy"]["n"] == 2
    assert rec["legacy"]["avg"] == -250.0
    assert rec["legacy"]["rule"] == "CLOSE_DTE=3"


def test_paper_record_with_no_current_trades_is_safe(tmp_path):
    from learning import seven_dte_forward as sdf

    class _Rec:
        def get_all_trades(self):
            return [{"dte_bucket": "7DTE", "entry_date": "2026-08-01 09:45 AM EST",
                     "pnl_dollars": -300.0}]

    rec = sdf.paper_record(_Rec())
    assert rec["n"] == 0 and rec["win_pct"] == 0.0
    assert not rec["meets_bar"]
    assert rec["legacy"]["n"] == 1
