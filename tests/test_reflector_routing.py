"""Tests for reflector routing (Phase 4a item 5)."""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.reflector import Reflector


@pytest.fixture
def isolated_reflector(monkeypatch, tmp_path):
    """A Reflector with mock deps and a temp LOG_DIR."""
    monkeypatch.setattr("config.LOG_DIR", str(tmp_path))
    kb     = MagicMock()
    preds  = MagicMock()
    plans  = MagicMock()
    trades = MagicMock()
    preds.get.return_value      = {"direction": "UP"}
    preds.accuracy.return_value = {"all": 0.55}
    plans.get_plan.return_value = {}
    kb.recent.return_value      = []
    trades.get_all_trades.return_value = []
    trades.get_trades_by.return_value  = []
    yield Reflector(
        knowledge_base=kb, prediction_log=preds,
        plan_logger=plans, trade_recorder=trades,
        api_key="fake_key",
    )


def test_normal_day_routes_to_phi4(isolated_reflector, monkeypatch):
    """When is_anomalous_day returns False, call_llm receives model_preference='phi4_first'."""
    monkeypatch.setattr(
        "learning.reflector.is_anomalous_day", lambda facts: False
    )
    with patch("learning.reflector.call_llm",
               return_value='{"summary":"ok","narrative":"-","kb_entries":[]}') as cm:
        isolated_reflector.reflect_today(today=date(2026, 5, 27))
        assert cm.called
        kwargs = cm.call_args.kwargs
        assert kwargs.get("model_preference") == "phi4_first"


def test_anomalous_day_routes_to_sonnet(isolated_reflector, monkeypatch):
    """When is_anomalous_day returns True, call_llm omits phi4_first preference."""
    monkeypatch.setattr(
        "learning.reflector.is_anomalous_day", lambda facts: True
    )
    with patch("learning.reflector.call_llm",
               return_value='{"summary":"ok","narrative":"-","kb_entries":[]}') as cm:
        isolated_reflector.reflect_today(today=date(2026, 5, 27))
        kwargs = cm.call_args.kwargs
        assert kwargs.get("model_preference") in (None, "sonnet_first")


def test_routing_recorded_in_result(isolated_reflector, monkeypatch):
    """The result dict must include the route used (telemetry)."""
    monkeypatch.setattr(
        "learning.reflector.is_anomalous_day", lambda facts: True
    )
    with patch("learning.reflector.call_llm",
               return_value='{"summary":"ok","narrative":"-","kb_entries":[]}'):
        result = isolated_reflector.reflect_today(today=date(2026, 5, 27))
        assert result.get("route") in ("sonnet_anomaly", "sonnet_fallback", "sonnet_anomaly_error")


def test_regime_change_walkback_survives_thanksgiving_long_weekend(monkeypatch, tmp_path):
    """After Thanksgiving (Thu+Fri closed), Monday's reflector must walk back
    >=5 days to find Wednesday's prediction."""
    monkeypatch.setattr("config.LOG_DIR", str(tmp_path))

    # Set up: today is Monday after Thanksgiving 2026-11-30; Wednesday 2026-11-25 had a prediction
    preds = MagicMock()

    def _get(date_str):
        if date_str == "2026-11-25":
            return {"regime": "TRENDING_UP_CALM"}
        return None  # Thu, Fri, Sat, Sun all missing

    preds.get.side_effect = _get

    r = Reflector(prediction_log=preds)

    # Mock date.today() to return Monday after Thanksgiving
    # The _regime_changed_vs_yesterday imports date from datetime inside the method
    mock_today = date(2026, 11, 30)  # Monday after Thanksgiving

    with patch("datetime.date") as mock_date_cls:
        # Make the class callable to construct date objects normally
        mock_date_cls.side_effect = lambda *a, **kw: date(*a, **kw)
        # Override just the today() method
        mock_date_cls.today.return_value = mock_today

        # Today's prediction says regime is RANGE_HIGH_VOL (different from Wed's TRENDING_UP_CALM)
        today_pred = {"regime": "RANGE_HIGH_VOL"}

        assert r._regime_changed_vs_yesterday(today_pred) is True
        # Verify the prediction lookup happened against 2026-11-25 (delta=5)
        preds.get.assert_any_call("2026-11-25")
