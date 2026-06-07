"""tests/test_dipbuy_signal_study.py -- Phase 1 dip-buy signal event-study."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest


def test_config_has_dipbuy_thresholds():
    import config
    assert config.DIPBUY_MIN_EDGE_PCT == 0.25
    assert config.DIPBUY_MIN_OOS_YEAR_FRAC == 0.60
    assert config.DIPBUY_MIN_TRIGGERS_PER_WINDOW == 5
    assert config.DIPBUY_IV_STRESS_MULT == 1.25
    assert config.DIPBUY_FWD_HORIZONS == (3, 5, 10)
