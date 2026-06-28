"""tests/test_directional_forecast.py -- independent next-day directional forecast.

The daily prediction used to just mirror the strategy (condor -> "neutral") and
score neutral on a broken +-0.25% one-day flatness test. This is the replacement:
a transparent technical lean (MA stack + momentum + RSI) INDEPENDENT of what we
trade, plus a VIX-scaled "flat" band so a neutral call is scored sanely.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd


def _df(kind, n=260, start=400.0):
    idx = pd.bdate_range(end="2026-06-26", periods=n)
    if kind == "up":
        close = start + np.arange(n) * 0.6
    elif kind == "down":
        close = start + (n - np.arange(n)) * 0.6
    else:  # flat / no-lean: tiny alternating noise, no sustained trend
        close = start + (np.arange(n) % 2) * 0.2
    return pd.DataFrame({"close": close}, index=idx)


def test_forecast_bullish_in_uptrend():
    from signals.directional_forecast import forecast_direction
    f = forecast_direction(_df("up"), vix=15)
    assert f["direction"] == "bullish"
    assert f["confidence"] > 0.5
    assert f["reasons"]


def test_forecast_bearish_in_downtrend():
    from signals.directional_forecast import forecast_direction
    assert forecast_direction(_df("down"), vix=20)["direction"] == "bearish"


def test_forecast_neutral_when_flat_with_sane_band():
    from signals.directional_forecast import forecast_direction
    f = forecast_direction(_df("flat"), vix=18)
    assert f["direction"] == "neutral"
    # the whole point: a real band, NOT the old hardcoded 0.25%
    assert f["expected_move_pct"] > 0.5


def test_expected_move_band_scales_with_vix():
    from signals.directional_forecast import forecast_direction
    hi = forecast_direction(_df("flat"), vix=30)["expected_move_pct"]
    lo = forecast_direction(_df("flat"), vix=12)["expected_move_pct"]
    assert hi > lo


def test_forecast_is_independent_of_any_strategy_arg():
    # it takes only price + vix — it cannot mirror the strategy because it never
    # sees one. (Guards against regressing to the old strategy-mirror behaviour.)
    from signals.directional_forecast import forecast_direction
    import inspect
    params = list(inspect.signature(forecast_direction).parameters)
    assert "strategy" not in params and "options" not in params
