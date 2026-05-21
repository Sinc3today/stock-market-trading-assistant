"""
tests/test_regime_detector.py — Test RegimeDetector

No live API calls — all tests use synthetic price data.

Run with:
    pytest tests/test_regime_detector.py -v
"""

import pytest
import sys
import os
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.regime_detector import RegimeDetector, Regime


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

def _make_df(trend: str = "up", bars: int = 250) -> pd.DataFrame:
    """
    Generate synthetic SPY daily data.
    trend="up"   → strong upward drift, ADX > 20
    trend="down" → strong downward drift, ADX > 20
    trend="flat" → pure sine-wave oscillation around a fixed mean,
                   producing near-zero net DM → ADX < 20
    """
    rng = np.random.default_rng(42)
    if trend == "up":
        closes = 400 + np.cumsum(rng.normal(0.35, 1.2, bars))
    elif trend == "down":
        closes = 500 - np.cumsum(rng.normal(0.35, 1.2, bars))
    else:
        # Strictly alternating +0.05 / -0.05 bars → +DM and -DM cancel
        # producing ADX ≈ 0, which reliably triggers the choppy branch.
        closes = np.full(bars, 450.0)
        for i in range(1, bars):
            closes[i] = closes[i - 1] + (0.05 if i % 2 == 0 else -0.05)
    return pd.DataFrame({
        "open":   closes - 0.5,
        "high":   closes + 1.0,
        "low":    closes - 1.0,
        "close":  closes,
        "volume": rng.integers(50_000_000, 100_000_000, bars),
    })


@pytest.fixture
def detector():
    return RegimeDetector()


@pytest.fixture
def up_df():
    return _make_df("up")


@pytest.fixture
def down_df():
    return _make_df("down")


@pytest.fixture
def flat_df():
    return _make_df("flat")


# ─────────────────────────────────────────
# SKIP / DATA VALIDATION
# ─────────────────────────────────────────

def test_event_day_skips(detector, up_df):
    """Event calendar date should always return EVENT_DAY / not tradeable."""
    today = date(2026, 5, 7)
    d = RegimeDetector(event_calendar=[today])
    result = d.classify(up_df, vix_current=13.0, ivr_current=25, today=today)
    assert result.regime    == Regime.EVENT_DAY
    assert result.tradeable is False
    print(f"\n✅ Event day blocked: {result.play}")


def test_insufficient_data_returns_unknown(detector):
    """Fewer than 200 bars → UNKNOWN, not tradeable."""
    small_df = _make_df("up", bars=50)
    result   = detector.classify(small_df, vix_current=13.0, ivr_current=25)
    assert result.regime    == Regime.UNKNOWN
    assert result.tradeable is False
    print(f"\n✅ Insufficient data blocked: {result.reasons[0]}")


def test_none_df_returns_unknown(detector):
    result = detector.classify(None, vix_current=13.0, ivr_current=25)
    assert result.regime    == Regime.UNKNOWN
    assert result.tradeable is False
    print("\n✅ None DataFrame handled gracefully")


# ─────────────────────────────────────────
# TRENDING REGIMES
# ─────────────────────────────────────────

def test_trending_up_calm_low_ivr(detector, up_df):
    """Uptrend + calm VIX + low IVR → BULL CALL DEBIT SPREAD."""
    result = detector.classify(up_df, vix_current=13.5, ivr_current=22)
    assert result.regime    == Regime.TRENDING_UP_CALM
    assert result.tradeable is True
    assert "DEBIT SPREAD" in result.play.upper()
    print(f"\n✅ Uptrend calm low IVR → {result.play}")


def test_trending_up_calm_high_ivr_credit_when_flag_off(detector, up_df, monkeypatch):
    """Uptrend + calm VIX + high IVR → BULL PUT CREDIT SPREAD, but only when
    the debit-preference flag is off."""
    import config
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", False)
    result = detector.classify(up_df, vix_current=14.0, ivr_current=62)
    assert result.regime    == Regime.TRENDING_UP_CALM
    assert result.tradeable is True
    assert "CREDIT SPREAD" in result.play.upper()
    print(f"\n✅ Uptrend calm high IVR (flag off) → {result.play}")


def test_trending_up_calm_high_ivr_prefers_debit_by_default(detector, up_df, monkeypatch):
    """With PREFER_DEBIT_OVER_CREDIT on (default), high IVR still takes a
    bull call debit instead of a credit spread (user preference)."""
    import config
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", True)
    result = detector.classify(up_df, vix_current=14.0, ivr_current=62)
    assert result.regime    == Regime.TRENDING_UP_CALM
    assert result.tradeable is True
    assert "DEBIT SPREAD" in result.play.upper()
    assert "CREDIT" not in result.play.upper()
    print(f"\n✅ Uptrend calm high IVR (flag on) → {result.play}")


def test_trending_down_calm_low_ivr(detector, down_df):
    """Downtrend + calm VIX + low IVR → BEAR PUT DEBIT SPREAD."""
    result = detector.classify(down_df, vix_current=15.0, ivr_current=20)
    assert result.regime    == Regime.TRENDING_DOWN_CALM
    assert result.tradeable is True
    assert "BEAR" in result.play.upper()
    print(f"\n✅ Downtrend calm low IVR → {result.play}")


def test_trending_down_calm_high_ivr(detector, down_df, monkeypatch):
    """Downtrend + calm VIX + high IVR → BEAR CALL CREDIT SPREAD (flag off)."""
    import config
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", False)
    result = detector.classify(down_df, vix_current=16.0, ivr_current=65)
    assert result.regime    == Regime.TRENDING_DOWN_CALM
    assert result.tradeable is True
    assert "CREDIT SPREAD" in result.play.upper()
    print(f"\n✅ Downtrend calm high IVR (flag off) → {result.play}")


def test_trending_down_calm_high_ivr_prefers_debit_by_default(detector, down_df, monkeypatch):
    """With the debit-preference flag on (default), high IVR downtrend takes
    a bear put debit instead of a bear call credit spread."""
    import config
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", True)
    result = detector.classify(down_df, vix_current=16.0, ivr_current=65)
    assert result.regime    == Regime.TRENDING_DOWN_CALM
    assert result.tradeable is True
    assert "DEBIT SPREAD" in result.play.upper()
    assert "CREDIT" not in result.play.upper()


def test_trending_high_vol_is_skipped(detector, up_df):
    """Uptrend + elevated VIX → SKIP. TRENDING_HIGH_VOL has no backtested
    edge (19% win rate); trading it 'reduced size' cost -$4,600 / ~half the
    Sharpe over 5 years. Matches CLAUDE.md's documented tradeable=False."""
    result = detector.classify(up_df, vix_current=25.0, ivr_current=40)
    assert result.regime    == Regime.TRENDING_HIGH_VOL
    assert result.tradeable is False
    assert "skip" in result.play.lower() or "no edge" in result.play.lower()
    print(f"\n✅ High vol trending skipped → {result.play}")


def test_trending_high_vol_down_is_skipped(detector, down_df):
    """Downtrend + elevated VIX → SKIP, same no-edge rationale."""
    result = detector.classify(down_df, vix_current=25.0, ivr_current=40)
    assert result.regime    == Regime.TRENDING_HIGH_VOL
    assert result.tradeable is False
    print(f"\n✅ High vol downtrend skipped → {result.play}")


# ─────────────────────────────────────────
# ENTRY-TIMING GATE — extended-trend guard for bull puts
# (added 2026-05-19; derived from 2026-05-18 KB entry: entered bull put
#  at +9.3% above 200MA, SPY closed -0.19%, short strike $1.20 ITM same day.)
# ─────────────────────────────────────────

def _make_extended_uptrend_df(bars: int = 250, drift: float = 0.9) -> pd.DataFrame:
    """Steeper uptrend than the default `up` fixture — sits well above 200MA
    with ADX comfortably above the 25 trending threshold."""
    rng    = np.random.default_rng(42)
    closes = 400 + np.cumsum(rng.normal(drift, 1.2, bars))
    return pd.DataFrame({
        "open":   closes - 0.5,
        "high":   closes + 1.0,
        "low":    closes - 1.0,
        "close":  closes,
        "volume": rng.integers(50_000_000, 100_000_000, bars),
    })


def test_extended_uptrend_blocks_bull_put(detector):
    """SPY >8% above 200MA + high IVR → skip bull put (entry-timing risk)."""
    df     = _make_extended_uptrend_df()
    result = detector.classify(df, vix_current=14.0, ivr_current=62)
    assert result.metrics["ma200_dist_%"] > 8.0, (
        f"Fixture should produce extended uptrend, "
        f"got {result.metrics['ma200_dist_%']}% above 200MA"
    )
    assert result.regime    == Regime.TRENDING_UP_CALM
    assert result.tradeable is False
    assert "extended" in result.play.lower() or "pullback" in result.play.lower()
    print(f"\n✅ Extended uptrend bull put blocked → {result.play}")


def test_extended_uptrend_blocks_debit_too(detector):
    """Over-extension cap now applies to debit spreads as well, not just
    credit. A LOW-IVR (debit) day that's far above the 200MA still skips —
    the 5yr backtest showed bull debits >9% extended have negative
    expectancy (win rate 50%→60%, Sharpe 1.73→3.06 after capping)."""
    df     = _make_extended_uptrend_df()
    result = detector.classify(df, vix_current=14.0, ivr_current=22)
    assert result.metrics["ma200_dist_%"] > 9.0
    assert result.regime    == Regime.TRENDING_UP_CALM
    assert result.tradeable is False
    assert "extended" in result.play.lower() or "pullback" in result.play.lower()
    print(f"\n✅ Extended uptrend debit now blocked → {result.play}")


def test_uptrend_too_close_to_ma200_skips(detector, up_df, monkeypatch):
    """Trending up but ma_dist_% < MIN_TREND_SEPARATION_PCT → SKIP (no edge).

    Real-world fixtures don't easily combine high ADX with tight separation
    (those conditions tend to oppose each other), so we monkeypatch the
    threshold above the fixture's measured distance to force the path.
    """
    from signals import regime_detector as rd
    # Find the fixture's measured separation, then raise the threshold above
    # it so the skip path is guaranteed to fire.
    baseline = detector.classify(up_df, vix_current=14.0, ivr_current=40)
    monkeypatch.setattr(rd, "MIN_TREND_SEPARATION_PCT",
                        baseline.metrics["ma200_dist_%"] + 1.0)
    result = detector.classify(up_df, vix_current=14.0, ivr_current=40)
    assert result.regime    == Regime.UNKNOWN
    assert result.tradeable is False
    assert "200MA" in result.play
    print(f"\n✅ Too-close-to-MA200 skip fired: {result.play}")


def test_moderate_uptrend_high_ivr_tradeable_below_extension_cap(detector, up_df, monkeypatch):
    """SPY ≤8% above 200MA + high IVR → tradeable (extension gate doesn't
    fire). Structure is credit only when the debit-preference flag is off."""
    import config
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", False)
    result = detector.classify(up_df, vix_current=14.0, ivr_current=62)
    assert result.metrics["ma200_dist_%"] <= 8.0
    assert result.regime    == Regime.TRENDING_UP_CALM
    assert result.tradeable is True
    assert "CREDIT" in result.play.upper()
    print(f"\n✅ Moderate uptrend high IVR (flag off) → {result.play}")


# ─────────────────────────────────────────
# CHOPPY REGIMES
# ─────────────────────────────────────────

def test_choppy_calm_gets_iron_condor(detector, flat_df):
    """Low ADX + calm VIX → IRON CONDOR."""
    result = detector.classify(flat_df, vix_current=14.0, ivr_current=30)
    assert result.regime    == Regime.CHOPPY_LOW_VOL
    assert result.tradeable is True
    assert "CONDOR" in result.play.upper()
    print(f"\n✅ Choppy calm → {result.play}")


def test_choppy_high_vol_skips(detector, flat_df):
    """Low ADX + elevated VIX → SKIP, not tradeable."""
    result = detector.classify(flat_df, vix_current=25.0, ivr_current=55)
    assert result.regime    == Regime.CHOPPY_HIGH_VOL
    assert result.tradeable is False
    print(f"\n✅ Choppy high vol blocked: {result.play}")


def test_choppy_transition_zone_half_size(detector, flat_df):
    """VIX 18–22 chop → reduced condor, lower confidence."""
    result = detector.classify(flat_df, vix_current=20.0, ivr_current=40)
    assert result.tradeable is True
    assert result.confidence <= 0.6
    assert "REDUCED" in result.play.upper() or "HALF" in result.play.upper()
    print(f"\n✅ Transition zone → {result.play} (conf {result.confidence:.0%})")


# ─────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────

def test_metrics_populated(detector, up_df):
    """Result metrics should include all expected keys."""
    result = detector.classify(up_df, vix_current=14.0, ivr_current=30)
    for key in ("spy_close", "ma200", "ma200_dist_%", "adx", "vix", "ivr"):
        assert key in result.metrics, f"Missing metric: {key}"
    print(f"\n✅ Metrics: {result.metrics}")


def test_to_dict_serialisable(detector, up_df):
    """to_dict() should produce a plain dict with no Enum values."""
    result  = detector.classify(up_df, vix_current=14.0, ivr_current=30)
    as_dict = result.to_dict()
    assert isinstance(as_dict["regime"], str)
    assert isinstance(as_dict["tradeable"], bool)
    assert isinstance(as_dict["reasons"], list)
    print(f"\n✅ to_dict() clean: regime={as_dict['regime']}")


def test_confidence_in_range(detector, up_df):
    result = detector.classify(up_df, vix_current=14.0, ivr_current=30)
    assert 0.0 <= result.confidence <= 1.0
    print(f"\n✅ Confidence in range: {result.confidence:.2f}")


# ─────────────────────────────────────────
# ADX
# ─────────────────────────────────────────

def test_adx_positive(up_df):
    adx = RegimeDetector._compute_adx(up_df, period=14)
    assert adx > 0
    print(f"\n✅ ADX computed: {adx:.2f}")


def test_adx_insufficient_data_returns_zero():
    small = _make_df("up", bars=10)
    adx   = RegimeDetector._compute_adx(small, period=14)
    assert adx == 0.0
    print(f"\n✅ ADX graceful fallback: {adx}")
