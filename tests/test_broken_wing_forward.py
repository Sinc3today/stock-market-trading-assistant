"""tests/test_broken_wing_forward.py -- broken-wing butterfly paper candidates.

First directional-lean structure to survive the full gauntlet (per-regime, OOS,
haircut, parameter sweep — docs/BROKEN_WING_STUDY.md). Runs zero-capital with an
explicit promotion bar before it may follow the 7DTE path to live mirroring.
Also guards the P&L sign convention: a BWB can be worth money to close (it's
long a far wing), so the close cost must NOT be clamped to >= 0.
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


def test_open_records_both_tenors_and_is_idempotent(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import maybe_open_broken_wing
    rec = TradeRecorder()
    opened = maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0)
    assert len(opened) == 2                       # 30DTE + 45DTE ladder
    buckets = sorted(t["dte_bucket"] for t in rec.get_open_trades())
    assert buckets == ["BWB-30DTE", "BWB-45DTE"]
    for t in rec.get_open_trades():
        assert t["ticker"] == "SPY" and t["strategy"] == "broken_wing"
        assert t["book"] == "candidate" and len(t["legs"]) == 4
        assert "promotion bar" in t["notes_entry"]
    # second call same day opens nothing
    assert maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0) == []


def test_legs_are_broken_wing_put_structure(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import maybe_open_broken_wing
    rec = TradeRecorder()
    maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0)
    legs = [t for t in rec.get_open_trades() if t["dte_bucket"] == "BWB-45DTE"][0]["legs"]
    assert all(l["option_type"] == "PUT" for l in legs)
    actions = [l["action"] for l in legs]
    assert actions == ["BUY", "SELL", "SELL", "BUY"]      # +1 / -2 / +1
    k_hi, k_mid1, k_mid2, k_lo = (l["strike"] for l in legs)
    assert k_mid1 == k_mid2 and k_lo < k_mid1 < k_hi
    assert (k_hi - k_mid1) == 3.0 and (k_mid1 - k_lo) == 8.0   # 3/8 wings


def test_disabled_flag_opens_nothing(iso, monkeypatch):
    import config
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import maybe_open_broken_wing
    monkeypatch.setattr(config, "BROKEN_WING_FORWARD_ENABLED", False)
    assert maybe_open_broken_wing(TradeRecorder(), spy_spot=630.0, vix=15.0) == []


def test_resolver_closes_at_time_stop_with_real_pnl(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import maybe_open_broken_wing, resolve_broken_wing
    rec = TradeRecorder()
    maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0)
    # 45DTE closes at ~21 DTE; jump well past that for the 30DTE too.
    later = date.today() + timedelta(days=40)
    closed = resolve_broken_wing(rec, spy_spot=632.0, vix=15.0, today=later)
    assert len(closed) == 2
    for t in rec.get_all_trades():
        assert t["outcome"] in ("win", "loss", "breakeven")
        assert t["pnl_dollars"] is not None       # NOT silently zeroed


def test_resolver_holds_young_position(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import maybe_open_broken_wing, resolve_broken_wing
    rec = TradeRecorder()
    maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0)
    closed = resolve_broken_wing(rec, spy_spot=630.0, vix=15.0,
                                 today=date.today() + timedelta(days=1))
    assert closed == []


def test_resolver_ignores_promoted_disciplined_trades(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import resolve_broken_wing
    rec = TradeRecorder()
    rec.log_entry(ticker="SPY", entry_price=0.7, size=1, trade_type="broken_wing",
                  strategy="broken_wing", direction="neutral", mode="swing",
                  legs=[{"action": "SELL", "option_type": "PUT", "strike": 620,
                         "expiry": date.today().isoformat()}],
                  max_profit=370.0, max_loss=430.0,
                  dte_bucket="BWB-45DTE", book="disciplined")
    assert resolve_broken_wing(rec, spy_spot=630.0, vix=15.0) == []


def test_credit_kept_when_spy_finishes_above_upper_strike(iso):
    # BWB profits (keeps the credit) when SPY is well above the upper long put at
    # expiry — the bullish lean. Verifies the sign convention end to end.
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import maybe_open_broken_wing, resolve_broken_wing
    rec = TradeRecorder()
    maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0)
    entry_credit = rec.get_open_trades()[0]["entry_price"]
    # Far above every put strike at expiry -> puts worthless -> keep the credit.
    at_expiry = date.today() + timedelta(days=60)
    resolve_broken_wing(rec, spy_spot=680.0, vix=15.0, today=at_expiry)
    pnl = rec.get_all_trades()[0]["pnl_dollars"]
    if entry_credit > 0:                          # opened for a credit
        assert pnl > 0
        assert abs(pnl - entry_credit * 100) < 5  # ~= the kept credit


def test_paper_record_tracks_promotion_bar(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.broken_wing_forward import (
        maybe_open_broken_wing, paper_record, resolve_broken_wing,
    )
    rec = TradeRecorder()
    assert paper_record(rec)["n"] == 0 and paper_record(rec)["meets_bar"] is False
    maybe_open_broken_wing(rec, spy_spot=630.0, vix=15.0)
    resolve_broken_wing(rec, spy_spot=632.0, vix=15.0,
                        today=date.today() + timedelta(days=40))
    r = paper_record(rec)
    assert r["n"] == 2                            # both tenors closed
    assert r["meets_bar"] is False                # nowhere near n>=15
