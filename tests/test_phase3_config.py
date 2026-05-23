"""Phase 3: intraday entry pipeline constants are declared in config.py.

Pure data; consumers land in Task 2+."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_kill_switch_defaults_on():
    """First phase with live behavior change — flag exists, default True
    (changes ship on merge, kill-switch available for emergency revert)."""
    assert config.INTRADAY_PAPER_BROKER_ENABLED is True


def test_entry_tier_minimum_is_high():
    assert config.ENTRY_TIER_MINIMUM == "high"


def test_dte_morning_cutoff_is_1230_et():
    assert config.INTRADAY_DTE_MORNING_CUTOFF == "12:30"


def test_ultra_conviction_threshold_is_85():
    assert config.ULTRA_CONVICTION_DOUBLE_DTE_SCORE == 85


def test_per_combo_daily_cap_is_two():
    assert config.INTRADAY_PER_COMBO_DAILY_CAP == 2
