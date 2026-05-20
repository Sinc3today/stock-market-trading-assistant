"""
tests/test_learning_outcome_resolver.py -- OutcomeResolver with mocked Polygon.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.outcome_resolver import OutcomeResolver, format_resolved_message
from learning.predictions      import PredictionLog, Prediction
from learning.paper_broker     import PaperBroker
from journal.trade_recorder    import TradeRecorder
from learning.scheduler        import job_outcome_resolver


class FakePolygon:
    def __init__(self, close):
        self._close = close

    def get_bars(self, *args, **kwargs):
        return pd.DataFrame({"close": [self._close]})


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _seed_prediction(direction, entry=720.0, tradeable=True):
    pl = PredictionLog()
    pl.save(Prediction(
        date=date.today().isoformat(),
        regime="trending_up_calm",
        direction=direction,
        tradeable=tradeable,
        entry_spy=entry,
    ))


def test_bullish_correct(iso_dirs):
    _seed_prediction("bullish", entry=720.0)
    result = OutcomeResolver(polygon_client=FakePolygon(close=725.0)).resolve_today()
    assert result["resolved"] is True
    assert result["outcome"]  == "correct"
    assert PredictionLog().get(date.today().isoformat())["actual_move_pct"] > 0


def test_bullish_wrong(iso_dirs):
    _seed_prediction("bullish", entry=720.0)
    result = OutcomeResolver(polygon_client=FakePolygon(close=715.0)).resolve_today()
    assert result["outcome"] == "wrong"


def test_bearish_correct(iso_dirs):
    _seed_prediction("bearish", entry=720.0)
    result = OutcomeResolver(polygon_client=FakePolygon(close=715.0)).resolve_today()
    assert result["outcome"] == "correct"


def test_neutral_inside_tolerance(iso_dirs):
    _seed_prediction("neutral", entry=720.0)
    # 0.1% move is inside the 0.25% tolerance
    result = OutcomeResolver(polygon_client=FakePolygon(close=720.72)).resolve_today()
    assert result["outcome"] == "correct"


def test_neutral_outside_tolerance(iso_dirs):
    _seed_prediction("neutral", entry=720.0)
    result = OutcomeResolver(polygon_client=FakePolygon(close=725.0)).resolve_today()
    assert result["outcome"] == "wrong"


def test_skip_day_marks_skip(iso_dirs):
    _seed_prediction("neutral", entry=720.0, tradeable=False)
    result = OutcomeResolver(polygon_client=FakePolygon(close=720.0)).resolve_today()
    assert result["outcome"] == "skip"


def test_no_prediction(iso_dirs):
    result = OutcomeResolver(polygon_client=FakePolygon(close=720.0)).resolve_today()
    assert result["resolved"] is False
    assert result["reason"]   == "no prediction"


def test_idempotent_resolve(iso_dirs):
    _seed_prediction("bullish", entry=720.0)
    r1 = OutcomeResolver(polygon_client=FakePolygon(close=725.0)).resolve_today()
    r2 = OutcomeResolver(polygon_client=FakePolygon(close=999.0)).resolve_today()
    assert r1["outcome"] == "correct"
    # second call should bail out without re-resolving
    assert r2["reason"] == "already resolved"
    # close value stays at the first resolution
    assert PredictionLog().get(date.today().isoformat())["actual_close"] == 725.0


def test_format_resolved_message_correct(iso_dirs):
    _seed_prediction("bullish", entry=720.0)
    OutcomeResolver(polygon_client=FakePolygon(close=725.0)).resolve_today()
    msg = format_resolved_message(PredictionLog().get(date.today().isoformat()))
    assert "CORRECT" in msg
    assert "bullish" in msg
    assert "$720.00" in msg
    assert "$725.00" in msg
    assert "+0.69%" in msg


def test_format_resolved_message_wrong(iso_dirs):
    _seed_prediction("bullish", entry=720.0)
    OutcomeResolver(polygon_client=FakePolygon(close=710.0)).resolve_today()
    msg = format_resolved_message(PredictionLog().get(date.today().isoformat()))
    assert "WRONG" in msg
    assert "-1.39%" in msg


def test_format_resolved_message_skip(iso_dirs):
    _seed_prediction("neutral", entry=720.0, tradeable=False)
    OutcomeResolver(polygon_client=FakePolygon(close=720.0)).resolve_today()
    msg = format_resolved_message(PredictionLog().get(date.today().isoformat()))
    assert "skip day" in msg


def test_scheduler_job_pings_post_fn(iso_dirs):
    _seed_prediction("bullish", entry=720.0)
    captured = []
    job_outcome_resolver(
        polygon_client=FakePolygon(close=725.0),
        post_fn=lambda body: captured.append(body),
    )
    assert len(captured) == 1
    assert "CORRECT" in captured[0]


def test_scheduler_job_skips_post_fn_when_unresolved(iso_dirs):
    # No prediction seeded -> resolve fails -> post_fn should not fire
    captured = []
    job_outcome_resolver(
        polygon_client=FakePolygon(close=725.0),
        post_fn=lambda body: captured.append(body),
    )
    assert captured == []


def test_snapshots_open_paper_trade(iso_dirs):
    # First, register an auto-paper trade
    play = {
        "date":      date.today().isoformat(),
        "tradeable": True,
        "regime":    "trending_up_calm",
        "confidence": 0.8,
        "reasons":    ["x"],
        "metrics":    {"spy_close": 720.0},
        "options":    {"strategy": "debit_spread", "net_debit": 3.0,
                       "max_profit": 700, "max_loss": 300, "legs": []},
    }
    PaperBroker().execute(play)
    OutcomeResolver(polygon_client=FakePolygon(close=725.0)).resolve_today()

    notes = TradeRecorder().get_all_trades()[0]["notes_entry"]
    assert "[MTM" in notes
    assert "725" in notes


def test_open_position_marked_with_real_close_on_skip_day(iso_dirs):
    """Regression: a skip day must still mark open [AUTO-PAPER] positions
    with the real SPY close, not 'no SPY data'. Previously resolve_today()
    short-circuited on skip days and passed spy_close=None."""
    # Open a paper position from a prior tradeable play.
    play = {
        "date":      "2026-05-18",
        "tradeable": True,
        "regime":    "trending_up_calm",
        "confidence": 0.85,
        "reasons":    ["x"],
        "metrics":    {"spy_close": 739.0},
        "options":    {"strategy": "credit_spread", "net_credit": 1.0,
                       "max_profit": 100, "max_loss": 400, "legs": []},
    }
    PaperBroker().execute(play)

    # Today's prediction is a SKIP, but SPY data is available.
    _seed_prediction("bullish", entry=739.0, tradeable=False)
    result = OutcomeResolver(polygon_client=FakePolygon(close=733.73)).resolve_today()
    assert result["outcome"] == "skip"

    notes = TradeRecorder().get_all_trades()[0]["notes_entry"]
    assert "no SPY data" not in notes
    assert "733.73" in notes


def test_skip_day_stores_real_move_for_scoring(iso_dirs):
    """A skip day must record the real close + move so skip_quality can
    score it. Previously actual_close was hardcoded to 0.0."""
    # Baseline price persisted on the skip prediction (entry_spy).
    _seed_prediction("bullish", entry=737.80, tradeable=False)
    OutcomeResolver(polygon_client=FakePolygon(close=733.73)).resolve_today()
    rec = PredictionLog().get(date.today().isoformat())
    assert rec["outcome"]      == "skip"
    assert rec["actual_close"] == 733.73
    assert rec["actual_move_pct"] < 0          # SPY fell vs baseline
    # And the skip is scored as a right call (declined a bullish trade,
    # SPY fell, so standing down avoided a loss).
    assert PredictionLog().skip_quality()["right"] == 1
