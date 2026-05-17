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

from learning.outcome_resolver import OutcomeResolver
from learning.predictions      import PredictionLog, Prediction
from learning.paper_broker     import PaperBroker
from journal.trade_recorder    import TradeRecorder


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
