"""
tests/test_learning_skip_scoring.py -- score_skip + PredictionLog.skip_quality.

Skips are scored SEPARATELY from prediction accuracy so standing down can't
inflate the headline directional number.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.predictions import PredictionLog, Prediction, score_skip


@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


# ── score_skip pure logic ──────────────────────────────

def test_score_skip_bullish_avoided_loss_is_right():
    # Declined a bullish trade and SPY fell → right call.
    assert score_skip("bullish", -0.55) == "right"


def test_score_skip_bullish_missed_gain():
    # Declined a bullish trade and SPY rose → missed opportunity.
    assert score_skip("bullish", 0.80) == "missed"


def test_score_skip_bullish_flat_is_neutral():
    assert score_skip("bullish", 0.03) == "neutral"


def test_score_skip_bearish_inverts():
    assert score_skip("bearish", 0.80)  == "right"    # avoided short-side loss
    assert score_skip("bearish", -0.80) == "missed"


def test_score_skip_neutral_condor_breakout_is_right():
    # A condor skip is right when SPY made a big move (would've breached).
    assert score_skip("neutral", 1.20)  == "right"
    assert score_skip("neutral", -1.20) == "right"
    # In-range → condor would've won, so skipping it was a miss.
    assert score_skip("neutral", 0.20)  == "missed"


# ── skip_quality aggregate ─────────────────────────────

def _resolved_skip(pl: PredictionLog, day: str, direction: str,
                   entry: float, close: float):
    pl.save(Prediction(date=day, regime="trending_up_calm",
                        direction=direction, tradeable=False, entry_spy=entry))
    pl.mark_resolved(day, close, "skip", day)


def test_skip_quality_empty(iso_logs):
    out = PredictionLog().skip_quality()
    assert out["sample"] == 0
    assert out["right_pct"] == 0.0


def test_skip_quality_counts_right_and_missed(iso_logs):
    pl = PredictionLog()
    _resolved_skip(pl, "2026-05-19", "bullish", 737.80, 733.73)  # fell → right
    _resolved_skip(pl, "2026-05-20", "bullish", 733.73, 740.00)  # rose → missed
    out = pl.skip_quality()
    assert out["right"]  == 1
    assert out["missed"] == 1
    assert out["right_pct"] == 50.0


def test_skip_quality_excludes_unscored_skips(iso_logs):
    """A skip with no actual_move_pct (e.g. data unavailable) is excluded."""
    pl = PredictionLog()
    # entry_spy=None → move_pct stays None → not counted
    pl.save(Prediction(date="2026-05-21", regime="event_day",
                        direction="bullish", tradeable=False, entry_spy=None))
    pl.mark_resolved("2026-05-21", 0.0, "skip", "2026-05-21")
    assert pl.skip_quality()["sample"] == 0


def test_skip_quality_does_not_pollute_accuracy(iso_logs):
    """Skips must never count toward the directional accuracy number."""
    pl = PredictionLog()
    _resolved_skip(pl, "2026-05-19", "bullish", 737.80, 733.73)
    # accuracy only scores correct/wrong, so a lone skip → empty sample
    assert pl.accuracy()["sample"] == 0
    assert pl.skip_quality()["sample"] == 1
