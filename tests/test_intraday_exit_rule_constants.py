"""Phase 1: per-sub-strategy exit-rule constants are declared in config.py.

Nothing CONSUMES them in Phase 1 — they're foundation. Phase 2's strategy-aware
ExitManager refactor will read them. We test their presence + sane defaults.
"""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_45dte_constants_match_current_defaults():
    # 45DTE: keep today's values (no live behavior change in Phase 1).
    assert config.PROFIT_TARGET_PCT_45DTE_CALL   == 0.70
    assert config.PROFIT_TARGET_PCT_45DTE_PUT    == 0.70
    assert config.PROFIT_TARGET_PCT_45DTE_COND   == 0.70
    assert config.DTE_CLOSE_THRESHOLD_45DTE      == 21
    # Experimental 45DTE stop (default None = no stop, matches current behavior).
    assert config.STOP_PCT_45DTE                  is None


def test_1_3dte_constants_are_aggressive():
    assert config.PROFIT_TARGET_PCT_1_3DTE_CALL  == 0.50
    assert config.PROFIT_TARGET_PCT_1_3DTE_PUT   == 0.50
    assert config.PROFIT_TARGET_PCT_1_3DTE_COND  == 0.50
    assert config.STOP_PCT_1_3DTE_CALL           == 0.50
    assert config.STOP_PCT_1_3DTE_PUT            == 0.50
    # condor exits on short-strike touch + force-close before bell
    assert config.FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE == 30
    assert config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_1_3DTE     is True


def test_0dte_constants_are_most_aggressive():
    # 0DTE: target 100% (credit doubled) for debits, 30% for condors (faster).
    assert config.PROFIT_TARGET_PCT_0DTE_CALL    == 1.00
    assert config.PROFIT_TARGET_PCT_0DTE_PUT     == 1.00
    assert config.PROFIT_TARGET_PCT_0DTE_COND    == 0.30
    assert config.STOP_PCT_0DTE_CALL             == 0.75
    assert config.STOP_PCT_0DTE_PUT              == 0.75
    # Force-close times of day (gamma risk into the bell). HH:MM strings, ET.
    assert config.FORCED_CLOSE_TIME_0DTE_DEBIT   == "15:30"
    assert config.FORCED_CLOSE_TIME_0DTE_CONDOR  == "15:00"
    assert config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_0DTE      is True
