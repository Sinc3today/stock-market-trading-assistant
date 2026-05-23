"""Phase 1: TUNABLE_PARAMS includes per-sub-strategy exit-rule constants."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.hypothesis_engine import TUNABLE_PARAMS


def test_existing_params_still_whitelisted():
    # Sanity: don't accidentally drop entries during the refactor.
    assert ("signals.regime_detector", "ADX_TREND_MIN")          in TUNABLE_PARAMS
    assert ("signals.regime_detector", "EXTENDED_TREND_MAX_PCT") in TUNABLE_PARAMS
    assert ("learning.exit_manager",   "PROFIT_TARGET_PCT")      in TUNABLE_PARAMS


def test_45dte_stop_added_with_bounds_and_nullable():
    rule = TUNABLE_PARAMS[("config", "STOP_PCT_45DTE")]
    assert rule["type"]     == "float_or_none"   # special: None = disable
    assert rule["min"]      == 0.60
    assert rule["max"]      == 0.90


def test_0dte_exit_rules_whitelisted():
    for var in ("PROFIT_TARGET_PCT_0DTE_CALL", "PROFIT_TARGET_PCT_0DTE_PUT",
                "PROFIT_TARGET_PCT_0DTE_COND",
                "STOP_PCT_0DTE_CALL", "STOP_PCT_0DTE_PUT"):
        assert ("config", var) in TUNABLE_PARAMS, var
    targets = TUNABLE_PARAMS[("config", "PROFIT_TARGET_PCT_0DTE_CALL")]
    assert targets["type"] == "float"
    assert targets["min"] >= 0.20 and targets["max"] <= 2.00


def test_1_3dte_exit_rules_whitelisted():
    for var in ("PROFIT_TARGET_PCT_1_3DTE_CALL", "PROFIT_TARGET_PCT_1_3DTE_PUT",
                "PROFIT_TARGET_PCT_1_3DTE_COND",
                "STOP_PCT_1_3DTE_CALL", "STOP_PCT_1_3DTE_PUT"):
        assert ("config", var) in TUNABLE_PARAMS, var


def test_45dte_profit_targets_whitelisted():
    for var in ("PROFIT_TARGET_PCT_45DTE_CALL", "PROFIT_TARGET_PCT_45DTE_PUT",
                "PROFIT_TARGET_PCT_45DTE_COND"):
        assert ("config", var) in TUNABLE_PARAMS, var
