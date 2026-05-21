"""
tests/test_learning_exit_manager.py -- mid-life profit-target / time-stop exits.

Uses synthetic open [AUTO-PAPER] trades and a fixed SPY/VIX so the
Black-Scholes mark is deterministic.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.exit_manager import ExitManager, bs_price
import learning.exit_manager as em
from journal.trade_recorder import TradeRecorder
from learning.paper_broker  import AUTO_TAG


class FakePolygon:
    def __init__(self, close): self._close = close
    def get_bars(self, *a, **k): return pd.DataFrame({"close": [self._close]})


class FakeVix:
    def __init__(self, v): self._v = v
    def get_current(self): return self._v


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _open_credit_spread(tr: TradeRecorder, exp: str, short_k=739.0, long_k=734.0,
                        credit=1.0, max_profit=100, max_loss=400):
    """A bull put credit spread: sell the higher put, buy the lower put."""
    return tr.log_entry(
        ticker="SPY", entry_price=credit, size=1,
        trade_type="credit_spread", strategy="credit_spread", direction="bullish",
        legs=[
            {"action": "SELL", "type": "put", "strike": short_k, "expiration": exp},
            {"action": "BUY",  "type": "put", "strike": long_k,  "expiration": exp},
        ],
        max_profit=max_profit, max_loss=max_loss,
        notes=f"{AUTO_TAG} test",
    )


# ── Black-Scholes sanity ───────────────────────────────

def test_bs_price_falls_back_to_intrinsic_at_expiry():
    # T=0 → intrinsic only
    assert bs_price("call", 745, 740, 0.0, 0.18) == 5.0
    assert bs_price("put",  735, 740, 0.0, 0.18) == 5.0
    assert bs_price("call", 730, 740, 0.0, 0.18) == 0.0


def test_bs_put_more_valuable_when_deeper_itm():
    # A 740 put is worth more when SPY is lower.
    near = bs_price("put", 738, 740, 30/365, 0.18)
    deep = bs_price("put", 730, 740, 30/365, 0.18)
    assert deep > near


# ── Profit target ──────────────────────────────────────

def test_profit_target_closes_when_spread_decays(iso_dirs, monkeypatch):
    """Credit spread far OTM near expiry → cheap to buy back → target hit."""
    tr = TradeRecorder()
    exp = (date.today() + timedelta(days=3)).isoformat()
    tid = _open_credit_spread(tr, exp)
    # SPY well above the 739 short put, 3 DTE, calm vol → buyback near 0,
    # so ~full credit captured (>70%).
    closed = ExitManager(
        polygon_client=FakePolygon(760.0), vix_client=FakeVix(14.0),
        trade_recorder=tr,
    ).manage_open(today=date.today())
    assert len(closed) == 1
    assert "profit target" in closed[0]["reason"] or "time stop" in closed[0]["reason"]
    assert tr.get_trade_by_id(tid)["outcome"] != "open"


# ── Time stop ──────────────────────────────────────────

def test_time_stop_closes_near_expiry(iso_dirs):
    """Even if the profit target isn't hit, <=21 DTE forces a close."""
    tr = TradeRecorder()
    exp = (date.today() + timedelta(days=10)).isoformat()
    _open_credit_spread(tr, exp)
    closed = ExitManager(
        polygon_client=FakePolygon(738.0),  # near the short strike, not a clean win
        vix_client=FakeVix(16.0), trade_recorder=tr,
    ).manage_open(today=date.today())
    assert len(closed) == 1
    assert "time stop" in closed[0]["reason"]


# ── No hard stop: losers ride ──────────────────────────

def test_losing_position_with_long_dte_is_not_closed(iso_dirs):
    """A position that's underwater but far from expiry must NOT be stopped
    out -- it rides (validated by the 5/18 recovery)."""
    tr = TradeRecorder()
    exp = (date.today() + timedelta(days=44)).isoformat()
    _open_credit_spread(tr, exp)
    # SPY below the short strike → spread is a loser, but 44 DTE and not
    # near the profit target.
    closed = ExitManager(
        polygon_client=FakePolygon(730.0), vix_client=FakeVix(20.0),
        trade_recorder=tr,
    ).manage_open(today=date.today())
    assert closed == []
    assert tr.get_trade_by_id(tr.get_all_trades()[0]["trade_id"])["outcome"] == "open"


# ── Slippage is applied against us ─────────────────────

def test_slippage_makes_credit_buyback_more_expensive(iso_dirs, monkeypatch):
    """For a credit spread we BUY back to close, so slippage should increase
    the exit cost (reducing P&L)."""
    monkeypatch.setattr(em, "EXIT_SLIPPAGE", 0.25)
    tr = TradeRecorder()
    exp = (date.today() + timedelta(days=30)).isoformat()
    mgr = ExitManager(polygon_client=FakePolygon(745.0), vix_client=FakeVix(16.0),
                      trade_recorder=tr)
    legs = [
        {"action": "SELL", "type": "put", "strike": 739.0, "expiration": exp},
        {"action": "BUY",  "type": "put", "strike": 734.0, "expiration": exp},
    ]
    px_with    = mgr._mark_exit_price("credit_spread", legs, 745.0, 16.0, date.today(), 30)
    monkeypatch.setattr(em, "EXIT_SLIPPAGE", 0.0)
    px_without = mgr._mark_exit_price("credit_spread", legs, 745.0, 16.0, date.today(), 30)
    assert px_with == pytest.approx(px_without + 0.25, abs=1e-6)


# ── Expired positions are left to ExpiryResolver ───────

def test_expired_position_left_for_expiry_resolver(iso_dirs):
    tr = TradeRecorder()
    exp = (date.today() - timedelta(days=1)).isoformat()  # already expired
    _open_credit_spread(tr, exp)
    closed = ExitManager(
        polygon_client=FakePolygon(745.0), vix_client=FakeVix(15.0),
        trade_recorder=tr,
    ).manage_open(today=date.today())
    assert closed == []   # ExitManager skips; ExpiryResolver will handle it
