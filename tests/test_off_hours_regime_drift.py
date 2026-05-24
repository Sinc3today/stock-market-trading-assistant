"""Tests for off-hours regime-drift detection (Phase 4a item 6)."""
import os
import sys
import tempfile
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.off_hours_learner import (
    OffHoursLearner,
    compute_distribution,
    detect_shifts,
    compute_feature_trends,
)


def test_compute_distribution_pct_sums_to_100():
    rows = [
        {"regime": "TRENDING_UP_CALM"},
        {"regime": "TRENDING_UP_CALM"},
        {"regime": "CHOPPY_LOW_VOL"},
        {"regime": "CHOPPY_LOW_VOL"},
    ]
    dist = compute_distribution(rows)
    assert dist["TRENDING_UP_CALM"] == pytest.approx(50.0)
    assert dist["CHOPPY_LOW_VOL"]   == pytest.approx(50.0)
    assert sum(dist.values()) == pytest.approx(100.0)


def test_compute_distribution_empty():
    assert compute_distribution([]) == {}


def test_detect_shifts_above_threshold():
    prior  = {"A": 50.0, "B": 30.0, "C": 20.0}
    recent = {"A": 30.0, "B": 30.0, "C": 40.0}
    shifts = detect_shifts(prior, recent, threshold_pct=10.0)
    keys = {s["regime"] for s in shifts}
    assert "A" in keys  # -20
    assert "C" in keys  # +20
    assert "B" not in keys  # 0


def test_detect_shifts_below_threshold_empty():
    prior  = {"A": 50.0, "B": 50.0}
    recent = {"A": 55.0, "B": 45.0}
    shifts = detect_shifts(prior, recent, threshold_pct=10.0)
    assert shifts == []


def test_compute_feature_trends_returns_means():
    rows = [
        {"vix": 14.0, "adx": 22.0, "ma200_dist": 3.0},
        {"vix": 15.0, "adx": 24.0, "ma200_dist": 3.5},
        {"vix": 16.0, "adx": 26.0, "ma200_dist": 4.0},
    ]
    trends = compute_feature_trends(rows)
    assert trends["vix_mean"]   == pytest.approx(15.0)
    assert trends["adx_mean"]   == pytest.approx(24.0)
    assert trends["ma200_dist_mean"] == pytest.approx(3.5)


def test_run_writes_report_when_classifications_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    kb = MagicMock()
    kb.append = MagicMock(return_value="kb_xx01")

    # Build 130 days of fake classifications (prior 60 + recent 60 + buffer)
    base = date.today() - timedelta(days=170)
    fake_rows = []
    for i in range(130):
        d = base + timedelta(days=i)
        # First 65 days: lots of TRENDING_UP_CALM; last 65 days: lots of RANGE_HIGH_VOL
        regime = "TRENDING_UP_CALM" if i < 65 else "RANGE_HIGH_VOL"
        fake_rows.append({"date": d, "regime": regime, "vix": 14.0, "adx": 22.0,
                           "ma200_dist": 3.0})

    learner = OffHoursLearner(knowledge_base=kb, api_key="fake_key")
    with patch.object(learner, "_load_regime_classifications", return_value=fake_rows), \
         patch("learning.off_hours_learner.call_llm",
               return_value='{"kb_entries":[{"category":"market_context","claim":"shift detected","evidence":"TRENDING_UP_CALM dropped 30%","confidence":0.75}]}'):
        result = learner.run(today=date.today())
    assert "shift_count" in result
    assert result["shift_count"] >= 1   # at least TRENDING_UP_CALM or RANGE_HIGH_VOL crosses threshold
    assert kb.append.called


def test_run_with_no_shifts_still_calls_claude(monkeypatch, tmp_path):
    """Per spec: silent regimes are info too — Sonnet still produces an entry."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    kb = MagicMock()
    kb.append = MagicMock(return_value="kb_xx02")
    fake_rows = []
    for i in range(130):
        d = date.today() - timedelta(days=170 - i)
        fake_rows.append({"date": d, "regime": "TRENDING_UP_CALM",
                          "vix": 14.0, "adx": 22.0, "ma200_dist": 3.0})

    learner = OffHoursLearner(knowledge_base=kb, api_key="fake_key")
    with patch.object(learner, "_load_regime_classifications", return_value=fake_rows), \
         patch("learning.off_hours_learner.call_llm",
               return_value='{"kb_entries":[{"category":"market_context","claim":"stable","evidence":"no shifts","confidence":0.6}]}') as cm:
        result = learner.run(today=date.today())
    assert cm.called
    assert result["shift_count"] == 0


def test_run_insufficient_history_skips_call(monkeypatch, tmp_path):
    """If <60 trading days available, skip the Claude call."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    kb = MagicMock()
    fake_rows = [
        {"date": date.today() - timedelta(days=i), "regime": "TRENDING_UP_CALM",
         "vix": 14.0, "adx": 22.0, "ma200_dist": 3.0}
        for i in range(40)
    ]
    learner = OffHoursLearner(knowledge_base=kb, api_key="fake_key")
    with patch.object(learner, "_load_regime_classifications", return_value=fake_rows), \
         patch("learning.off_hours_learner.call_llm") as cm:
        result = learner.run(today=date.today())
    assert result.get("skipped") is True
    assert not cm.called
