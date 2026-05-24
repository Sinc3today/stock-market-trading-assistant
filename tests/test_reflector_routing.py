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
