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


# ── triggers ─────────────────────────────────────────────────

def test_rsi_series_oversold_on_persistent_decline():
    from backtests.dipbuy_signal_study import rsi_series
    # 10 up days then a long 30-day decline → Wilder's avg-gain bleeds off,
    # RSI ends well below 30.
    closes = list(range(100, 110)) + list(range(109, 79, -1))
    s = pd.Series(closes, dtype=float)
    rsi = rsi_series(s, period=14)
    assert rsi.iloc[-1] < 30


def test_oversold_triggers_fire_only_on_fresh_cross():
    from backtests.dipbuy_signal_study import oversold_triggers
    rsi = pd.Series([35, 28, 27, 31, 29], dtype=float)
    trig = oversold_triggers(rsi, threshold=30)
    assert list(trig) == [False, True, False, False, True]


def test_pullback_triggers_require_uptrend_and_fresh_dip():
    from backtests.dipbuy_signal_study import pullback_triggers
    close = pd.Series([110, 109, 108], dtype=float)
    ma20  = pd.Series([107, 109, 109], dtype=float)
    ma200 = pd.Series([100, 100, 100], dtype=float)
    trig  = pullback_triggers(close, ma20, ma200)
    assert list(trig) == [False, False, True]
