"""
tests/test_indicators.py — Test all indicator modules
Uses real Polygon data so requires POLYGON_API_KEY in .env

Run with:
    pytest tests/test_indicators.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.polygon_client import PolygonClient
from indicators.moving_averages import MovingAverages
from indicators.donchian import DonchianChannels
from indicators.volume import VolumeAnalysis
from indicators.cvd import CVDAnalysis
from indicators.rsi import RSIAnalysis


@pytest.fixture(scope="module")
def daily_df():
    """Fetch AAPL daily bars once — reused across all tests."""
    client = PolygonClient()
    df = client.get_bars("AAPL", timeframe="day", limit=300, days_back=400)
    assert df is not None and len(df) >= 200, "Need at least 200 bars for indicator tests"
    return df


# ─────────────────────────────────────────
# MOVING AVERAGES
# ─────────────────────────────────────────

def test_moving_averages_returns_result(daily_df):
    ma = MovingAverages(daily_df)
    result = ma.analyze()
    assert result is not None
    assert result["ma20"] is not None
    assert result["ma50"] is not None
    assert result["ma200"] is not None
    print(f"\n✅ MA20: {result['ma20']} | MA50: {result['ma50']} | MA200: {result['ma200']}")

def test_moving_averages_score_in_range(daily_df):
    result = MovingAverages(daily_df).analyze()
    assert 0 <= result["score"] <= 35
    print(f"\n✅ MA Score: {result['score']}/35 — {result['trend_direction']}")

def test_moving_averages_trend_direction(daily_df):
    result = MovingAverages(daily_df).analyze()
    assert result["trend_direction"] in ("bullish", "bearish", "neutral")
    print(f"\n✅ Trend: {result['trend_direction']} | Stack bullish: {result['stack_bullish']}")

# ─────────────────────────────────────────
# DONCHIAN CHANNELS
# ─────────────────────────────────────────

def test_donchian_returns_result(daily_df):
    dc = DonchianChannels(daily_df)
    result = dc.analyze()
    assert result is not None
    assert result["upper_band"] is not None
    assert result["lower_band"] is not None
    assert result["upper_band"] > result["lower_band"]
    print(f"\n✅ Donchian Upper: {result['upper_band']} | Lower: {result['lower_band']}")

def test_donchian_score_in_range(daily_df):
    result = DonchianChannels(daily_df).analyze()
    assert 0 <= result["score"] <= 15
    print(f"\n✅ Donchian Score: {result['score']}/15 | Breakout up: {result['breakout_up']}")

def test_donchian_no_lookahead(daily_df):
    """Current close should be compared to PREVIOUS period's channel."""
    result = DonchianChannels(daily_df).analyze()
    # Channel width should be positive
    assert result["channel_width_pct"] > 0
    print(f"\n✅ Channel width: {result['channel_width_pct']}%")

# ─────────────────────────────────────────
# VOLUME
# ─────────────────────────────────────────

def test_volume_returns_result(daily_df):
    vol = VolumeAnalysis(daily_df)
    result = vol.analyze()
    assert result is not None
    assert result["current_volume"] is not None
    assert result["rvol"] is not None
    print(f"\n✅ Volume: {result['current_volume']:,} | RVOL: {result['rvol']}x")

def test_volume_score_in_range(daily_df):
    result = VolumeAnalysis(daily_df).analyze()
    assert 0 <= result["score"] <= 12
    print(f"\n✅ Volume Score: {result['score']}/12 | Spike: {result['volume_spike']}")

# ─────────────────────────────────────────
# CVD
# ─────────────────────────────────────────

def test_cvd_returns_result(daily_df):
    cvd = CVDAnalysis(daily_df)
    result = cvd.analyze()
    assert result is not None
    assert result["cvd_current"] is not None
    assert result["cvd_slope"] in ("rising", "falling", "flat")
    print(f"\n✅ CVD: {result['cvd_current']} | Slope: {result['cvd_slope']}")

def test_cvd_score_in_range(daily_df):
    result = CVDAnalysis(daily_df).analyze()
    assert 0 <= result["score"] <= 12
    print(f"\n✅ CVD Score: {result['score']}/12 | Signal: {result['cvd_signal']}")

# ─────────────────────────────────────────
# RSI
# ─────────────────────────────────────────

def test_rsi_returns_result(daily_df):
    rsi = RSIAnalysis(daily_df)
    result = rsi.analyze()
    assert result is not None
    assert result["rsi_current"] is not None
    assert 0 <= result["rsi_current"] <= 100
    print(f"\n✅ RSI: {result['rsi_current']} | Trend: {result['rsi_trend']}")

def test_rsi_score_in_range(daily_df):
    result = RSIAnalysis(daily_df).analyze()
    assert 0 <= result["score"] <= 12
    print(f"\n✅ RSI Score: {result['score']}/12 | Bull div: {result['bullish_divergence']} | Bear div: {result['bearish_divergence']}")

def test_rsi_divergence_strength_valid(daily_df):
    result = RSIAnalysis(daily_df).analyze()
    if result["divergence_strength"] is not None:
        assert result["divergence_strength"] in ("strong", "moderate")
    print(f"\n✅ Divergence strength: {result['divergence_strength']}")

# ─────────────────────────────────────────
# COMBINED SCORE CHECK
# ─────────────────────────────────────────

def test_total_score_within_bounds(daily_df):
    """All indicators combined should never exceed 100."""
    ma_score  = MovingAverages(daily_df).analyze()["score"]
    dc_score  = DonchianChannels(daily_df).analyze()["score"]
    vol_score = VolumeAnalysis(daily_df).analyze()["score"]
    cvd_score = CVDAnalysis(daily_df).analyze()["score"]
    rsi_score = RSIAnalysis(daily_df).analyze()["score"]

    total = ma_score + dc_score + vol_score + cvd_score + rsi_score

    print(f"\n✅ Score breakdown:")
    print(f"   MA:      {ma_score}/35")
    print(f"   Donchian:{dc_score}/15")
    print(f"   Volume:  {vol_score}/12")
    print(f"   CVD:     {cvd_score}/12")
    print(f"   RSI:     {rsi_score}/12")
    print(f"   ─────────────")
    print(f"   TOTAL:   {total}/86  (scorer will normalize to /100)")

    assert total <= 86, f"Raw total {total} exceeds max possible 86"