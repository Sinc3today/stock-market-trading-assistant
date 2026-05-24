"""
tests/test_predictions_per_substrategy.py -- PredictionLog.accuracy(by_substrategy=True)

Phase 4a Task 4: rolling_accuracy per-sub-strategy.
Tests that accuracy() can return per-strategy breakdowns keyed by
"strategy:dte_bucket:book" with an "all" aggregate.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.predictions import PredictionLog, Prediction


@pytest.fixture
def log(tmp_path, monkeypatch):
    """Isolated PredictionLog in a temp directory."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return PredictionLog()


def _add_prediction(
    log: PredictionLog,
    date_str: str,
    outcome: str | None,        # "correct" / "wrong" / "skip" / None (unresolved)
    strategy: str | None = None,
    dte_bucket: str | None = None,
    book: str | None = None,
    direction: str = "bullish",
):
    """Helper to create and optionally resolve a prediction."""
    p = Prediction(
        date=date_str,
        regime="trending_up_calm",
        direction=direction,
        tradeable=outcome != "skip",
        entry_spy=500.0,
        strategy=strategy,
        dte_bucket=dte_bucket,
        book=book,
    )
    log.save(p)
    if outcome is not None:
        log.mark_resolved(
            prediction_date=date_str,
            actual_close=505.0 if outcome == "correct" else 495.0,
            outcome=outcome,
            resolution_date=date_str,
        )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_accuracy_aggregate_returns_dict(log):
    """Default behavior (by_substrategy=False) preserved — returns a dict with
    "sample", "accuracy", etc. (the existing shape)."""
    today = date.today().isoformat()
    _add_prediction(log, today, "correct", strategy="iron_condor", dte_bucket="45DTE", book="disciplined")

    result = log.accuracy(n=30)
    # Default: returns the aggregate dict unchanged
    assert isinstance(result, dict)
    assert "sample" in result
    assert "accuracy" in result


def test_accuracy_by_substrategy_returns_dict(log):
    """by_substrategy=True returns a dict with sub-strategy keys and 'all' aggregate.
    Sub-strategies with < MIN_SAMPLES (3) resolved entries are excluded.
    """
    base = date.today()

    # 4 correct iron_condor / 45DTE / disciplined → should appear in result
    for i in range(4):
        d = (base - timedelta(days=i)).isoformat()
        _add_prediction(log, d, "correct", strategy="iron_condor", dte_bucket="45DTE", book="disciplined")

    # 2 wrong bull_debit / 1-3DTE / learning → below MIN_SAMPLES, should be excluded
    for i in range(2):
        d = (base - timedelta(days=10 + i)).isoformat()
        _add_prediction(log, d, "wrong", strategy="bull_debit", dte_bucket="1-3DTE", book="learning")

    result = log.accuracy(n=60, by_substrategy=True)

    assert isinstance(result, dict)
    # "all" aggregate must be present
    assert "all" in result
    assert isinstance(result["all"], dict)
    assert "accuracy" in result["all"]
    assert "sample" in result["all"]

    # The condor sub-strategy (4 samples) must appear
    condor_key = "iron_condor:45DTE:disciplined"
    assert condor_key in result, f"Expected '{condor_key}' in {list(result.keys())}"
    assert result[condor_key]["sample"] == 4
    assert result[condor_key]["accuracy"] == 100.0

    # The bull_debit sub-strategy (only 2 samples) must be excluded
    bull_key = "bull_debit:1-3DTE:learning"
    assert bull_key not in result, f"'{bull_key}' should be excluded (< 3 samples)"


def test_accuracy_by_substrategy_excludes_unresolved(log):
    """Unresolved predictions (no outcome) must not count in any sub-strategy bucket."""
    base = date.today()

    # 3 resolved correct entries
    for i in range(3):
        d = (base - timedelta(days=i)).isoformat()
        _add_prediction(log, d, "correct", strategy="iron_condor", dte_bucket="45DTE", book="disciplined")

    # 5 unresolved entries for same sub-strategy (should be ignored)
    for i in range(5):
        d = (base - timedelta(days=10 + i)).isoformat()
        _add_prediction(log, d, None, strategy="iron_condor", dte_bucket="45DTE", book="disciplined")

    result = log.accuracy(n=60, by_substrategy=True)

    assert isinstance(result, dict)
    condor_key = "iron_condor:45DTE:disciplined"
    assert condor_key in result
    # Only the 3 resolved ones should count
    assert result[condor_key]["sample"] == 3
    assert result["all"]["sample"] == 3
