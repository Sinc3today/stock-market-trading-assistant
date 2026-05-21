"""
tests/test_timeframes.py -- multi-timeframe track registry + per-track backtest.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.timeframes import (
    TRACKS, get_track, enabled_tracks, daily_backtestable_tracks, TimeframeTrack,
)
from backtests.realistic_pricing import run_realistic_backtest


# ── Registry ───────────────────────────────────────────

def test_registry_has_four_tracks():
    names = {t.name for t in TRACKS}
    assert names == {"0DTE", "1DTE", "5DTE", "45DTE"}


def test_intraday_tracks_disabled_and_not_daily_backtestable():
    for name in ("0DTE", "1DTE"):
        t = get_track(name)
        assert t.requires_intraday is True
        assert t.enabled is False
        assert t.daily_backtestable is False


def test_daily_tracks_enabled_and_backtestable():
    for name in ("5DTE", "45DTE"):
        t = get_track(name)
        assert t.requires_intraday is False
        assert t.enabled is True
        assert t.daily_backtestable is True


def test_enabled_and_daily_accessors():
    assert {t.name for t in enabled_tracks()}            == {"5DTE", "45DTE"}
    assert {t.name for t in daily_backtestable_tracks()} == {"5DTE", "45DTE"}


def test_profit_targets_scale_with_dte():
    """Shorter DTE should take profit sooner (lower target) than longer DTE."""
    tg = {t.name: t.profit_target_pct for t in TRACKS}
    assert tg["0DTE"] <= tg["1DTE"] <= tg["5DTE"] <= tg["45DTE"]


def test_get_track_case_insensitive():
    assert get_track("5dte").name == "5DTE"
    with pytest.raises(KeyError):
        get_track("3DTE")


# ── Per-track backtest threading ───────────────────────

def _ramp_df(n=160, start=500.0, step=0.8):
    d0 = date(2025, 1, 1)
    idx = [pd.Timestamp(d0 + timedelta(days=i)) for i in range(n)]
    return pd.DataFrame({"close": [start + step * i for i in range(n)]}, index=idx)


def test_track_params_change_hold_length():
    """A 5DTE track should hold far shorter than a 45DTE track on the same
    signal stream — proving per-track params thread through the engine."""
    df = _ramp_df()
    regime = pd.DataFrame([
        {"date": d, "play": "bull_debit", "tradeable": True} for d in df.index
    ])
    short = run_realistic_backtest(df, regime, max_concurrent=1, track=get_track("5DTE"))
    long  = run_realistic_backtest(df, regime, max_concurrent=1, track=get_track("45DTE"))
    assert len(short) > 0 and len(long) > 0
    assert short["days_held"].mean() < long["days_held"].mean()
    # Shorter holds free the single slot faster → more trades.
    assert len(short) > len(long)
