"""Per-sub-strategy exit-rule constants declared in config.py.

The original docstring said these were "foundation" that a later refactor would
read. For the profit-target / stop / DTE-threshold constants that happened. For
CONDOR_SHORT_STRIKE_TOUCH_EXIT_* and FORCED_CLOSE_* it never did — they sat
declared and asserted for months while ExitManager._evaluate read none of them,
so config and this file both reported a feature that did not exist. They were
removed 2026-09-07; those assertions went with them.

Caution about the tests that remain: asserting a constant equals its own
declared value cannot fail for a real reason. They are worth keeping only as a
change-detector on numbers that DO drive behaviour — which is now all of them.
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


def test_0dte_constants_are_most_aggressive():
    # 0DTE: target 100% (credit doubled) for debits, 30% for condors (faster).
    assert config.PROFIT_TARGET_PCT_0DTE_CALL    == 1.00
    assert config.PROFIT_TARGET_PCT_0DTE_PUT     == 1.00
    assert config.PROFIT_TARGET_PCT_0DTE_COND    == 0.30
    assert config.STOP_PCT_0DTE_CALL             == 0.75
    assert config.STOP_PCT_0DTE_PUT              == 0.75
    # Force-close times of day (gamma risk into the bell). HH:MM strings, ET.
