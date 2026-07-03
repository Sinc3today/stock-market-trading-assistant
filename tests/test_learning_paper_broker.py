"""
tests/test_learning_paper_broker.py -- PaperBroker auto-execution.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.paper_broker import PaperBroker, AUTO_TAG
from learning.predictions  import PredictionLog
from journal.trade_recorder import TradeRecorder
from journal.plan_logger    import PlanLogger


@pytest.fixture(autouse=True)
def _entry_window_open(monkeypatch):
    """Neutralize the 09:45-15:00 ET entry-window guard so open-logic tests don't
    depend on wall-clock time. The guard itself is covered by test_entry_window.py."""
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _tradeable_play():
    return {
        "date":       date.today().isoformat(),
        "tradeable":  True,
        "regime":     "trending_up_calm",
        "confidence": 0.85,
        "reasons":    ["ADX 28 trend", "VIX 14 calm", "above 200MA"],
        "metrics":    {"spy_close": 720.0, "vix": 14.2, "ivr": 32.0, "adx": 28.0},
        "options": {
            "strategy":  "debit_spread",
            "legs":      [{"strike": 720, "side": "buy"}, {"strike": 730, "side": "sell"}],
            "max_profit": "$700",
            "max_loss":   "$300",
            "rr_ratio":   2.3,
            "recommended_dte": 21,
            "net_debit":  3.00,
        },
    }


def _skip_play():
    return {
        "date":       date.today().isoformat(),
        "tradeable":  False,
        "regime":     "event_day",
        "confidence": 0.0,
        "reasons":    ["FOMC today"],
        "metrics":    {},
        "options":    {},
    }


def test_tradeable_play_records_trade_and_prediction(isolated_dirs):
    broker = PaperBroker()
    result = broker.execute(_tradeable_play())

    assert result["recorded"] is True
    assert result["trade_id"]

    trades = TradeRecorder().get_all_trades()
    assert len(trades) == 1
    t = trades[0]
    assert AUTO_TAG in t["notes_entry"]
    assert t["ticker"]    == "SPY"
    assert t["strategy"]  == "debit_spread"
    assert t["direction"] == "BULLISH"

    pred = PredictionLog().get(date.today().isoformat())
    assert pred is not None
    assert pred["direction"] == "bullish"
    assert pred["tradeable"] is True
    assert pred["entry_spy"] == 720.0


def test_prediction_uses_attached_forecast_not_strategy_mirror(isolated_dirs):
    # The independent directional forecast must win over the strategy's implied
    # direction. Guard against the wiring silently reverting to _infer_direction.
    play = _tradeable_play()                      # debit_spread -> mirror says bullish
    play["forecast"] = {"direction": "bearish", "expected_move_pct": 1.2,
                        "confidence": 0.7, "reasons": ["MA stack down"]}
    PaperBroker().execute(play)
    pred = PredictionLog().get(date.today().isoformat())
    assert pred["direction"] == "bearish"         # forecast, NOT the strategy mirror
    assert pred["expected_move_pct"] == 1.2       # band persisted for neutral-scoring


def test_prediction_falls_back_when_no_forecast(isolated_dirs):
    play = _tradeable_play()                       # no forecast attached
    PaperBroker().execute(play)
    pred = PredictionLog().get(date.today().isoformat())
    assert pred["direction"] == "bullish"          # strategy-mirror fallback
    assert pred["expected_move_pct"] is None


def test_plan_to_play_carries_forecast_both_paths():
    # THE silent-drop point: the forecast must survive the plan -> play reshape,
    # for tradeable AND skip plans, or the prediction loses it.
    fc = {"direction": "bearish", "expected_move_pct": 1.1}
    tradeable = {"date": "2026-07-02", "strategy": "iron_condor",
                 "legs": [{"strike": 700}], "regime": "choppy_low_vol", "forecast": fc}
    assert PaperBroker._plan_to_play(tradeable)["forecast"] == fc
    skip = {"date": "2026-07-02", "action": "SKIP", "forecast": fc}
    assert PaperBroker._plan_to_play(skip)["forecast"] == fc


def test_format_plan_persists_forecast():
    # The other drop point: the plan written to spy_daily_plans.json must include
    # the forecast so the broker (which reads the stored plan) can use it.
    from types import SimpleNamespace
    from signals.spy_daily_strategy import SPYDailyStrategy
    rr = SimpleNamespace(regime=SimpleNamespace(value="choppy_low_vol"),
                         play="Iron condor", confidence=0.8,
                         metrics={"spy_close": 740.0}, reasons=["a", "b"])
    fc = {"direction": "neutral", "expected_move_pct": 1.2}
    plan = SPYDailyStrategy._format_plan(date.today(), rr,
                                         {"strategy": "iron_condor", "legs": []}, fc)
    assert plan["forecast"] == fc


def test_skip_day_logs_prediction_only(isolated_dirs):
    result = PaperBroker().execute(_skip_play())
    assert result["recorded"] is False
    assert result["trade_id"] is None
    assert TradeRecorder().get_all_trades() == []

    pred = PredictionLog().get(date.today().isoformat())
    assert pred is not None
    assert pred["tradeable"] is False
    assert pred["direction"] == "neutral"


def test_execute_today_reads_plan_logger(isolated_dirs):
    plans = PlanLogger()
    plans.save_plan({
        "date":   date.today().isoformat(),
        "ticker": "SPY",
        "regime": "trending_up_calm",
        "play":   "BULL CALL DEBIT SPREAD",
        "confidence": 0.8,
        "strategy":   "debit_spread",
        "legs":       [{"strike": 720}],
        "max_profit": "$700",
        "max_loss":   "$300",
        "rr_ratio":   2.0,
        "recommended_dte": 21,
        "regime_metrics": {"spy_close": 720.0, "adx": 28.0, "vix": 14.0},
        "thesis": "ADX 28 trend | VIX 14 calm",
        "executed": False,
        "trade_id": None,
    })
    result = PaperBroker().execute_today()
    assert result["recorded"] is True
    assert TradeRecorder().get_all_trades()[0]["strategy"] == "debit_spread"

    # Plan should now be marked executed
    p = plans.get_today()
    assert p["executed"] is True
    assert p["trade_id"] == result["trade_id"]


def test_execute_today_handles_skip_plan(isolated_dirs):
    PlanLogger().save_plan({
        "date":   date.today().isoformat(),
        "ticker": "SPY",
        "action": "SKIP",
        "regime": "event_day",
        "reason": "FOMC day",
    })
    result = PaperBroker().execute_today()
    assert result["recorded"] is False
    assert PredictionLog().get(date.today().isoformat())["tradeable"] is False


def test_execute_today_no_plan(isolated_dirs):
    result = PaperBroker().execute_today()
    assert result == {"prediction_date": None, "trade_id": None, "recorded": False}
