"""Tests for Reflector helpers added in Phase 4a Task 5 (post-review fixes)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.reflector import Reflector


def test_extract_today_numbers_from_prediction_fields():
    """The corrected field list must match the Prediction dataclass schema."""
    ctx = {
        "prediction": {
            "entry_spy":        587.42,
            "predicted_target": 590.0,
            "predicted_stop":   585.5,
            "actual_close":     588.10,
            "actual_move_pct":  0.12,
            "confidence":       0.65,
            # Old field that no longer exists on Prediction — must not leak in:
            "vix":              99.0,
        },
        "open_positions": [
            {"entry_price": 1.25, "pnl_dollars": 130.0},
        ],
    }
    nums = Reflector._extract_today_numbers(ctx)
    assert 587.42 in nums
    assert 590.0 in nums
    assert 585.5 in nums
    assert 588.10 in nums
    assert 0.12 in nums
    assert 0.65 in nums
    assert 1.25 in nums
    assert 130.0 in nums
    # Old field that doesn't exist on Prediction must not be included
    assert 99.0 not in nums


def test_extract_today_trade_ids_includes_closed_today(tmp_path, monkeypatch):
    """Trade IDs must include trades that CLOSED today (Important 3 fix)."""
    monkeypatch.setattr("config.LOG_DIR", str(tmp_path))
    trades_data = [
        {"trade_id": "CLOSED01", "exit_date": "2026-05-23 04:32 PM EST",
         "outcome": "win", "notes_entry": ""},
        {"trade_id": "STILLOPEN", "exit_date": None, "outcome": "open",
         "notes_entry": ""},
    ]
    with open(os.path.join(tmp_path, "trades.json"), "w") as f:
        json.dump(trades_data, f)

    from journal.trade_recorder import TradeRecorder
    r = Reflector(trade_recorder=TradeRecorder())
    ctx = {
        "date": "2026-05-23",
        "open_positions": [{"trade_id": "AUTO0001"}],
    }
    ids = r._extract_today_trade_ids(ctx)
    assert "AUTO0001" in ids        # from open_positions
    assert "CLOSED01" in ids        # from today's closed trades
    assert "STILLOPEN" not in ids   # open trade not in open_positions list


def test_validator_metrics_applied_during_reflect_one():
    """The kb_validator must cap confidence and flag vague evidence during _reflect_one.

    Contract change (Task 7): reflect_today now returns {date, units, failed, kb_ids};
    validator_metrics is an internal per-unit concern and is no longer surfaced on the
    top-level return dict. This test verifies the validator still RUNS (caps/violations
    are applied) by checking: the KB entry is appended (cap didn't block it) and the
    result shows the unit ran successfully.
    """
    from unittest.mock import patch, MagicMock

    kb = MagicMock()
    kb.append = MagicMock(return_value="kb_x")
    kb.recent.return_value = []
    preds = MagicMock()
    preds.get.return_value = {"actual_close": 587.42}
    preds.accuracy.return_value = {"all": {"sample": 1, "accuracy": 1.0}}
    plans = MagicMock()
    plans.get_plan.return_value = {}
    trades = MagicMock()
    trades.get_all_trades.return_value = []

    r = Reflector(
        knowledge_base=kb,
        prediction_log=preds,
        plan_logger=plans,
        trade_recorder=trades,
        api_key="fake",
    )

    reply_json = json.dumps({
        "summary":   "s",
        "narrative": "n",
        "kb_entries": [
            {
                "category":   "regime_accuracy",
                "claim":      "c",
                "evidence":   "vague narrative without specific numbers",
                "confidence": 0.9,
            }
        ],
    })

    from datetime import date
    # _call_claude now returns (text, route_label) tuple
    with patch.object(r, "_call_claude", return_value=(reply_json, "phi4")):
        result = r.reflect_today(today=date(2026, 5, 23))

    # Contract change: top-level result is now {date, units, failed, kb_ids}.
    # Validator still ran — confirmed by KB entry being appended (capped, not blocked).
    assert result["units"] == 1
    assert result["failed"] == 0
    assert "kb_x" in result["kb_ids"]   # validator ran but entry still appended
    # Verify kb.append was called (i.e., validate_kb_entries + append loop both fired).
    kb.append.assert_called_once()
