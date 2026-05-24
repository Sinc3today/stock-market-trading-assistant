"""Tests for anomaly_detector (Phase 4a item 5)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.anomaly_detector import is_anomalous_day


def test_normal_day_not_anomalous():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.3,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is False


def test_two_stops_triggers_anomaly():
    facts = {
        "stops_today": 2,
        "prediction_miss_pct": 0.5,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_one_stop_below_threshold_not_anomalous(monkeypatch):
    monkeypatch.setattr(config, "REFLECTOR_ANOMALY_STOPS_MIN", 2)
    facts = {
        "stops_today": 1,
        "prediction_miss_pct": 0.5,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is False


def test_large_prediction_miss_triggers_anomaly():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 2.0,  # > default 1.5
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_negative_prediction_miss_uses_abs_value():
    """Q3 confirmed: absolute magnitude delta."""
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": -2.5,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_new_substrategy_triggers_anomaly():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.0,
        "new_substrategies_today": ["iron_condor_0DTE"],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_regime_change_triggers_anomaly():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.0,
        "new_substrategies_today": [],
        "regime_changed_today": True,
    }
    assert is_anomalous_day(facts) is True


def test_disabled_new_substrategy_flag_does_not_trigger(monkeypatch):
    monkeypatch.setattr(config, "REFLECTOR_ANOMALY_NEW_SUBSTRATEGY", False)
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.0,
        "new_substrategies_today": ["iron_condor_0DTE"],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is False


def test_missing_fields_default_safe():
    """Empty facts should not crash; default to not anomalous."""
    facts = {}
    assert is_anomalous_day(facts) is False
