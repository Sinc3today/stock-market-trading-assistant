"""
tests/test_exit_digest.py -- end-of-day disciplined-only exit digest.

job_exit_digest reads today's closed DISCIPLINED trades from the journal and
sends ONE consolidated push (learning-book exits stay silent; misleading
"target/stop hit" title replaced by a net-P&L summary).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest import mock
import pytz
from datetime import datetime

import learning.scheduler as sch
from learning.exit_manager import format_exit_digest_title


def _today():
    return datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")


# ── title formatter ──────────────────────────────────────────

def test_digest_title_summarizes_count_and_net():
    closed = [
        {"outcome": "win", "pnl_dollars": 205.0},
        {"outcome": "loss", "pnl_dollars": -136.0},
        {"outcome": "breakeven", "pnl_dollars": 0.0},
    ]
    title = format_exit_digest_title(closed)
    assert "3" in title
    assert "+$69" in title          # 205 - 136 + 0 = +69
    assert "target/stop hit" not in title


# ── job_exit_digest ──────────────────────────────────────────

def _patch_recorder(monkeypatch, trades):
    rec = mock.Mock(get_closed_trades=mock.Mock(return_value=trades))
    monkeypatch.setattr(sch, "TradeRecorder", mock.Mock(return_value=rec))
    monkeypatch.setattr(sch.config, "is_trading_day", lambda *_: True)


def test_digest_pushes_only_disciplined_closed_today(monkeypatch):
    today = _today()
    trades = [
        {"trade_id": "D1", "book": "disciplined", "exit_date": f"{today} 04:00 PM EST",
         "outcome": "win", "pnl_dollars": 205.0, "strategy": "iron_condor"},
        {"trade_id": "L1", "book": "learning", "exit_date": f"{today} 10:35 AM EST",
         "outcome": "breakeven", "pnl_dollars": 0.0, "strategy": "put_debit_spread"},
        {"trade_id": "OLD", "book": "disciplined", "exit_date": "2026-01-01 04:00 PM EST",
         "outcome": "loss", "pnl_dollars": -50.0, "strategy": "credit_spread"},
    ]
    _patch_recorder(monkeypatch, trades)
    pushes = []
    sch.job_exit_digest(play_fn=lambda **kw: pushes.append(kw))

    assert len(pushes) == 1
    body = pushes[0]["body"]
    assert "D1" in body            # disciplined today included
    assert "L1" not in body        # learning excluded
    assert "OLD" not in body       # prior day excluded
    assert "205" in pushes[0]["title"] or "+$205" in pushes[0]["title"]


def test_digest_no_push_when_nothing_closed_today(monkeypatch):
    _patch_recorder(monkeypatch, [
        {"trade_id": "L1", "book": "learning", "exit_date": f"{_today()} 10:00 AM EST",
         "outcome": "win", "pnl_dollars": 12.0},
    ])
    pushes = []
    sch.job_exit_digest(play_fn=lambda **kw: pushes.append(kw))
    assert pushes == []            # only a learning close today -> silent


def test_digest_skips_on_non_trading_day(monkeypatch):
    _patch_recorder(monkeypatch, [])
    monkeypatch.setattr(sch.config, "is_trading_day", lambda *_: False)
    pushes = []
    sch.job_exit_digest(play_fn=lambda **kw: pushes.append(kw))
    assert pushes == []
