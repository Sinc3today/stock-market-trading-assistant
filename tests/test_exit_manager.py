"""tests/test_exit_manager.py -- tests for exit_rule_for public accessor."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.exit_manager import exit_rule_for


def test_exit_rule_for_exposes_time_exit_keys():
    rule = exit_rule_for("put_debit_spread", "0DTE")
    assert "scratch_time" in rule
    assert "scratch_theta" in rule
    assert "hard_close_time" in rule
    assert rule["scratch_time"] is None
    assert rule["hard_close_time"] is None
