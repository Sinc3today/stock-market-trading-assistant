"""
tests/test_gates_earnings_reaction.py -- EARNINGS_REACTION_GATE_ENABLED behavior.

Verifies the gate consults EarningsHistory only when the flag is on, and
that "calm" reactors get a tighter block window while "volatile" / "normal"
keep the default. Failures in the history lookup fall back to the default
block so we never miss a real risk.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from signals.gates import AlertGates


class _StubCalendar:
    def __init__(self, entry): self._entry = entry
    def get_for_ticker(self, ticker, days):
        return self._entry


class _StubHistory:
    def __init__(self, stats=None, raises=False):
        self._stats  = stats
        self._raises = raises
    def get_reactions(self, ticker):
        if self._raises:
            raise RuntimeError("network down")
        return self._stats


def _gates(entry, history_stats=None, history_raises=False):
    return AlertGates(
        earnings_calendar = _StubCalendar(entry),
        earnings_history  = _StubHistory(history_stats, history_raises),
    )


# ── FLAG OFF: history is ignored entirely ─────────────

def test_flag_off_keeps_default_block_for_calm_ticker(monkeypatch):
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", False)
    g = _gates(
        entry         = {"earnings_date": "2026-05-18", "days_away": 1},
        history_stats = {"gap_class": "calm", "mean_abs_move_pct": 0.5,
                         "stdev_move_pct": 0.2},
    )
    blocked, msg, _ = g._check_earnings("AAPL")
    assert blocked is True
    assert "calm" not in msg  # no class label leaked when gate is off


# ── FLAG ON: per-class behavior ───────────────────────

def test_flag_on_calm_ticker_passes_outside_calm_window(monkeypatch):
    """Calm reactor 1 day from earnings → with CALM_WINDOW_DAYS=0, allowed."""
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "EARNINGS_CALM_WINDOW_DAYS", 0)
    g = _gates(
        entry         = {"earnings_date": "2026-05-18", "days_away": 1},
        history_stats = {"gap_class": "calm", "mean_abs_move_pct": 0.5,
                         "stdev_move_pct": 0.2},
    )
    blocked, msg, _ = g._check_earnings("AAPL")
    assert blocked is False


def test_flag_on_calm_ticker_still_blocked_on_earnings_day(monkeypatch):
    """Day-of earnings (days_away=0): even calm reactors are blocked."""
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "EARNINGS_CALM_WINDOW_DAYS", 0)
    g = _gates(
        entry         = {"earnings_date": "2026-05-17", "days_away": 0},
        history_stats = {"gap_class": "calm", "mean_abs_move_pct": 0.5,
                         "stdev_move_pct": 0.2},
    )
    blocked, msg, _ = g._check_earnings("AAPL")
    assert blocked is True
    assert "calm" in msg   # class label surfaced in suppression message


def test_flag_on_volatile_ticker_blocked_with_label(monkeypatch):
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", True)
    g = _gates(
        entry         = {"earnings_date": "2026-05-18", "days_away": 1},
        history_stats = {"gap_class": "volatile", "mean_abs_move_pct": 6.0,
                         "stdev_move_pct": 2.0},
    )
    blocked, msg, _ = g._check_earnings("TSLA")
    assert blocked is True
    assert "volatile" in msg


def test_flag_on_normal_ticker_uses_default_window(monkeypatch):
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", True)
    g = _gates(
        entry         = {"earnings_date": "2026-05-18", "days_away": 1},
        history_stats = {"gap_class": "normal", "mean_abs_move_pct": 2.5,
                         "stdev_move_pct": 1.0},
    )
    blocked, msg, _ = g._check_earnings("MSFT")
    assert blocked is True
    assert "normal" in msg


# ── SAFETY: history failure must not weaken the gate ──

def test_flag_on_history_lookup_failure_keeps_default_block(monkeypatch):
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", True)
    g = _gates(
        entry          = {"earnings_date": "2026-05-18", "days_away": 1},
        history_raises = True,
    )
    blocked, msg, _ = g._check_earnings("UNKN")
    assert blocked is True
    assert "alert suppressed" in msg
    # No class label since we never got a reaction class
    for label in ("calm", "normal", "volatile"):
        assert label not in msg


def test_flag_on_history_missing_ticker_keeps_default_block(monkeypatch):
    """Ticker not in 30-day cache → no reaction data, default block holds."""
    monkeypatch.setattr(config, "EARNINGS_REACTION_GATE_ENABLED", True)
    g = _gates(
        entry         = {"earnings_date": "2026-05-18", "days_away": 1},
        history_stats = None,
    )
    blocked, _, _ = g._check_earnings("UNKN")
    assert blocked is True
