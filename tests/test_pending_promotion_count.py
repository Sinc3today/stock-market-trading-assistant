"""Phase 1: when an accepted-verdict notification fires, include the count of
hypotheses currently pending promotion + an alert line at >= 5."""

import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.hypothesis_runner import _count_pending_promotions, _pending_alert_line


def _make_hyp(tmp_path, hid, status):
    p = tmp_path / f"hyp_{hid}.json"
    p.write_text(json.dumps({"id": hid, "status": status}))
    return p


def test_count_pending_returns_only_accepted(tmp_path):
    _make_hyp(tmp_path, "001", "proposed")
    _make_hyp(tmp_path, "002", "accepted")
    _make_hyp(tmp_path, "003", "accepted")
    _make_hyp(tmp_path, "004", "rejected")
    _make_hyp(tmp_path, "005", "inconclusive")
    assert _count_pending_promotions(str(tmp_path)) == 2


def test_count_returns_zero_for_empty_dir(tmp_path):
    assert _count_pending_promotions(str(tmp_path)) == 0


def test_count_returns_zero_for_missing_dir():
    assert _count_pending_promotions("/nonexistent/path/that/does/not/exist") == 0


def test_alert_line_below_threshold_is_empty():
    assert _pending_alert_line(4) == ""


def test_alert_line_at_or_above_threshold_warns():
    line = _pending_alert_line(5)
    assert "⚠️" in line
    assert "5" in line
    line10 = _pending_alert_line(10)
    assert "10" in line10
