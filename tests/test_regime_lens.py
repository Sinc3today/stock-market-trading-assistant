"""Phase 2b-1: regime-per-timeframe lens abstraction.

A thin wrapper over RegimeDetector (daily) + SPYOptionsEngine (intraday) that
each strategy declares its dependency on via the LENS_FOR_STRATEGY registry.
Formalization only — no functional change to existing behavior."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from signals.regime_lens import (
    RegimeLens, DailyLens, IntradayLens,
    LENS_FOR_STRATEGY, lens_for,
)


def test_lens_registry_maps_known_strategies():
    # 45DTE strategies use the daily lens.
    assert lens_for("call_debit_spread", "45DTE") is DailyLens
    assert lens_for("put_debit_spread",  "45DTE") is DailyLens
    assert lens_for("iron_condor",       "45DTE") is DailyLens
    # 1-3DTE + 0DTE use the intraday lens.
    assert lens_for("call_debit_spread", "1-3DTE") is IntradayLens
    assert lens_for("iron_condor",       "0DTE")   is IntradayLens
    assert lens_for("put_debit_spread",  "0DTE")   is IntradayLens


def test_lens_for_unknown_strategy_returns_none():
    """Unknown (strategy, dte_bucket) combinations return None — caller decides
    whether to default or error."""
    assert lens_for("some_new_strategy", "45DTE") is None
    assert lens_for("iron_condor",       "weird_bucket") is None


def test_daily_lens_wraps_RegimeDetector():
    """DailyLens.read() returns a RegimeResult — same shape RegimeDetector.classify produces."""
    from signals.regime_detector import RegimeResult, Regime
    import pandas as pd
    # Synthetic: 250 days of flat-then-up SPY so detector has enough data.
    n = 250
    closes = [500.0] * 150 + [500.0 + 0.5 * i for i in range(n - 150)]
    spy_df = pd.DataFrame({
        "close": closes,
        "high":  [c * 1.01 for c in closes],
        "low":   [c * 0.99 for c in closes],
    }, index=pd.date_range("2024-01-01", periods=n))
    result = DailyLens().read(spy_daily_df=spy_df, vix_current=17.0, ivr_current=40.0)
    assert isinstance(result, RegimeResult)
    assert isinstance(result.regime, Regime)


def test_intraday_lens_wraps_SPYOptionsEngine():
    """IntradayLens.read() returns a list[SPYSetup] — the engine's native output."""
    from signals.spy_options_engine import SPYSetup
    import pandas as pd
    # Synthetic minimal frames the engine can consume.
    n = 50
    df_15m = pd.DataFrame({
        "open": [500.0] * n, "high": [501.0] * n,
        "low":  [499.0] * n, "close": [500.0] * n,
        "volume": [1_000_000] * n,
    }, index=pd.date_range("2026-05-22 09:30", periods=n, freq="15min"))
    df_5m  = pd.DataFrame({
        "open": [500.0] * n, "high": [500.5] * n,
        "low":  [499.5] * n, "close": [500.0] * n,
        "volume": [333_000] * n,
    }, index=pd.date_range("2026-05-22 09:30", periods=n, freq="5min"))
    setups = IntradayLens().read(df_15m=df_15m, df_5m=df_5m)
    assert isinstance(setups, list)
    # Every emitted setup is a SPYSetup (might be empty list if scoring fails — fine).
    for s in setups:
        assert isinstance(s, SPYSetup)


def test_regime_lens_is_abc_with_read_method():
    """RegimeLens is the abstract base with a read() method both subclasses implement."""
    assert hasattr(RegimeLens, "read")
    assert hasattr(DailyLens, "read")
    assert hasattr(IntradayLens, "read")
