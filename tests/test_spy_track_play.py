"""
tests/test_spy_track_play.py -- track-aware play generation + scheduler wiring.

Verifies SPYDailyStrategy.build_today(track=...) tags the play and threads the
track DTE through OptionsLayer, and that register_spy_jobs adds a per-track
job for each enabled daily track besides 45DTE.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.spy_daily_strategy import SPYDailyStrategy
from signals.options_layer import OptionsLayer
from signals.timeframes import get_track


class _FakePolygon:
    def get_bars(self, *a, **k):
        rng = np.random.default_rng(1)
        # Steady uptrend, low noise → trending, moderate extension.
        closes = 400 + np.cumsum(rng.normal(0.35, 0.5, 260))
        return pd.DataFrame({
            "open": closes - 0.5, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": rng.integers(5e7, 1e8, 260),
        })


class _FakeVix:
    def get_current(self): return 14.0


class _FakeIvr:
    def get_iv_rank(self, ticker): return 30.0


def _strategy():
    return SPYDailyStrategy(
        polygon_client=_FakePolygon(), vix_client=_FakeVix(), ivr_client=_FakeIvr(),
    )


# ── event_calendar accepts the EventCalendar object (regression) ──────────

def test_strategy_accepts_event_calendar_object():
    """Live wiring passes an EventCalendar OBJECT (MorningBriefer needs it).
    SPYDailyStrategy must normalize it to block dates for RegimeDetector,
    which expects an iterable of dates — set(EventCalendar()) would TypeError."""
    from data.event_calendar import EventCalendar
    ec = EventCalendar()
    strat = SPYDailyStrategy(
        polygon_client=_FakePolygon(), vix_client=_FakeVix(), ivr_client=_FakeIvr(),
        event_calendar=ec,
    )
    # detector got the block-date set, not the object
    assert isinstance(strat.detector.event_calendar, set)
    assert strat.detector.event_calendar == set(ec.get_block_dates())


def test_strategy_still_accepts_event_date_list():
    """Back-compat: a plain list[date] must still work."""
    dates = [date(2026, 6, 17), date(2026, 6, 18)]
    strat = SPYDailyStrategy(
        polygon_client=_FakePolygon(), vix_client=_FakeVix(), ivr_client=_FakeIvr(),
        event_calendar=dates,
    )
    assert strat.detector.event_calendar == set(dates)


# ── OptionsLayer DTE override ──────────────────────────

def test_options_layer_dte_target_override():
    ol = OptionsLayer()
    base = ol.analyze("SPY", {"final_score": 85, "direction": "bullish"},
                      550, 560, 540, iv_rank=20)
    five = ol.analyze("SPY", {"final_score": 85, "direction": "bullish"},
                      550, 560, 540, iv_rank=20, dte_target=5)
    assert base["recommended_dte"] == 45      # mode-based default
    assert five["recommended_dte"] == 5        # override wins


# ── Track-aware build_today ────────────────────────────

def test_build_today_default_is_45dte():
    card = _strategy().build_today(today=date(2026, 5, 20))
    assert card["track"] == "45DTE"
    assert "[45DTE]" in card["discord_message"]


def test_build_today_5dte_track_tags_and_sets_dte():
    card = _strategy().build_today(today=date(2026, 5, 20), track=get_track("5DTE"))
    assert card["track"] == "5DTE"
    assert "[5DTE]" in card["discord_message"]
    # If it's a tradeable day, the option DTE should reflect the track.
    if card["tradeable"]:
        assert card["options"]["recommended_dte"] == 5
    # The plan payload is also tagged.
    assert card["plan_payload"].get("track") == "5DTE"


# ── Scheduler wiring ───────────────────────────────────

class _FakeScheduler:
    def __init__(self): self.jobs = []
    def add_job(self, func, trigger, **kw): self.jobs.append({"func": func, **kw})


def test_register_spy_jobs_adds_per_track_job():
    from scheduler.spy_daily_scheduler import register_spy_jobs
    s = _FakeScheduler()
    register_spy_jobs(
        scheduler=s, polygon_client=_FakePolygon(), vix_client=_FakeVix(),
        ivr_client=_FakeIvr(), post_fn=lambda m: None, event_calendar=[],
    )
    ids = {j["id"] for j in s.jobs}
    assert "spy_premarket" in ids               # 45DTE morning brief
    assert "spy_track_5dte" in ids              # the 5DTE track job
    # No intraday track jobs (not yet built).
    assert "spy_track_0dte" not in ids
    assert "spy_track_1dte" not in ids
