"""tests/test_qqq_condor_forward.py -- QQQ condor paper candidates (2026-07-09).

Failed the robustness bar for real money (docs/QQQ_CONDOR_TRANSFER.md); runs as
a zero-capital candidate with an explicit promotion bar (survive a regime change).
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
    from learning.qqq_condor_forward import maybe_open_qqq_condor, BUCKET
    rec = TradeRecorder()
    r = maybe_open_qqq_condor(rec, qqq_spot=560.0, vxn=22.0)
    assert r and r["recorded"]
    t = rec.get_open_trades()[0]
    assert t["ticker"] == "QQQ" and t["strategy"] == "iron_condor"
    assert t["book"] == "candidate" and t["dte_bucket"] == BUCKET
    assert t["entry_price"] > 0 and len(t["legs"]) == 4
    # idempotent per day
    assert maybe_open_qqq_condor(rec, qqq_spot=560.0, vxn=22.0) is None


def test_resolver_closes_at_time_stop(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.qqq_condor_forward import (
        maybe_open_qqq_condor, resolve_qqq_condors, CLOSE_DTE, DTE,
    )
    rec = TradeRecorder()
    maybe_open_qqq_condor(rec, qqq_spot=560.0, vxn=22.0)
    # expiry = nearest Friday in [today+45, today+51]; +31 days guarantees
    # dte_left <= 20 <= CLOSE_DTE regardless of which Friday it landed on
    later = date.today() + timedelta(days=DTE + 7 - CLOSE_DTE)
    closed = resolve_qqq_condors(rec, qqq_spot=560.0, vxn=22.0, today=later)
    assert len(closed) == 1
    t = rec.get_all_trades()[0]
    assert t["outcome"] in ("win", "loss", "breakeven")


def test_resolver_holds_when_young_and_below_target(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.qqq_condor_forward import maybe_open_qqq_condor, resolve_qqq_condors
    rec = TradeRecorder()
    maybe_open_qqq_condor(rec, qqq_spot=560.0, vxn=22.0)
    closed = resolve_qqq_condors(rec, qqq_spot=560.0, vxn=22.0,
                                 today=date.today() + timedelta(days=3))
    assert closed == []          # young + at-the-money -> keep holding
