"""tests/test_plan_to_play_guard.py -- a plan with no real options structure
must NOT open a position (regression: 2026-06-12 choppy_transition opened a
strategy='none', empty-legs, $1.00 placeholder trade)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from learning.paper_broker import PaperBroker


@pytest.fixture(autouse=True)
def _entry_window_open(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


def test_plan_with_strategy_none_is_not_tradeable():
    # the exact 2026-06-12 shape: non-SKIP plan but no structure
    plan = {"date": "2026-06-12", "regime": "choppy_transition",
            "strategy": "none", "legs": [], "thesis": "vol unclear",
            "confidence": 0.5, "regime_metrics": {"spy_close": 740.0}}
    play = PaperBroker._plan_to_play(plan)
    assert play["tradeable"] is False


def test_plan_with_empty_legs_is_not_tradeable():
    plan = {"date": "2026-06-12", "regime": "choppy_low_vol",
            "strategy": "iron_condor", "legs": [], "confidence": 0.7}
    play = PaperBroker._plan_to_play(plan)
    assert play["tradeable"] is False


def test_plan_with_real_structure_is_tradeable():
    plan = {"date": "2026-06-12", "regime": "choppy_low_vol",
            "strategy": "iron_condor",
            "legs": [{"strike": 700, "action": "sell"}, {"strike": 695, "action": "buy"}],
            "confidence": 0.7, "regime_metrics": {"spy_close": 740.0}}
    play = PaperBroker._plan_to_play(plan)
    assert play["tradeable"] is True


def test_skip_plan_still_not_tradeable():
    plan = {"date": "2026-06-12", "action": "SKIP", "reason": "extension gate",
            "regime": "trending_up_calm"}
    play = PaperBroker._plan_to_play(plan)
    assert play["tradeable"] is False


def test_execute_logs_prediction_but_no_open_when_no_structure(tmp_path, monkeypatch):
    """End-to-end: a no-structure play logs the prediction but opens nothing."""
    monkeypatch.setattr("config.LOG_DIR", str(tmp_path) + "/")
    from learning.predictions import PredictionLog
    play = {"date": "2026-06-12", "tradeable": True, "regime": "choppy_transition",
            "confidence": 0.5, "reasons": ["vol unclear"],
            "metrics": {"spy_close": 740.0},
            "options": {"strategy": "none", "legs": []}}
    res = PaperBroker().execute(play)
    assert res["trade_id"] is None                       # nothing opened
    assert PredictionLog().get("2026-06-12") is not None  # forecast still logged
