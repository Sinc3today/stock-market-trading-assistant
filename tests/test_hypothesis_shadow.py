"""tests/test_hypothesis_shadow.py — Task 5: tunable cap + shadow-pressure gate."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_extended_trend_cap_is_tunable():
    from learning.hypothesis_engine import TUNABLE_PARAMS
    rule = TUNABLE_PARAMS[("signals.regime_detector", "EXTENDED_TREND_MAX_PCT")]
    assert rule["type"] == "float"
    assert rule["min"] == 9.0 and rule["max"] == 15.0   # raise-only band, never below backtested 9.0


def test_shadow_pressure_flag(monkeypatch):
    import config
    monkeypatch.setattr(config, "SHADOW_MIN_DAYS", 10)
    monkeypatch.setattr(config, "SHADOW_MIN_WINRATE", 0.55)
    from learning.hypothesis_engine import shadow_under_pressure
    assert shadow_under_pressure({"n": 12, "closed_pnl": 300.0, "directional_win_rate": 0.6}) is True
    assert shadow_under_pressure({"n": 12, "closed_pnl": -50.0, "directional_win_rate": 0.6}) is False
    assert shadow_under_pressure({"n": 5,  "closed_pnl": 300.0, "directional_win_rate": 0.6}) is False
    assert shadow_under_pressure({"n": 12, "closed_pnl": 300.0, "directional_win_rate": 0.5}) is False
