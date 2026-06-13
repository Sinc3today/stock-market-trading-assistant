"""
tests/test_indicators_synthetic.py — Synthetic (no-network) unit tests for the
five technical-indicator modules.

These are CHARACTERIZATION tests: synthetic OHLCV DataFrames are built in code
(deterministic uptrend / downtrend / flat / dip / spike series), each indicator's
analyze() is run, and assertions are pinned to the ACTUAL observed output plus
directional behavior we construct on purpose.

No network, no PolygonClient, no live clients, no sleeps. Runs in well under 1s.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from indicators.moving_averages import MovingAverages
from indicators.donchian import DonchianChannels
from indicators.volume import VolumeAnalysis
from indicators.cvd import CVDAnalysis
from indicators.rsi import RSIAnalysis


# ---------------------------------------------------------------------------
# Synthetic OHLCV builders
# ---------------------------------------------------------------------------

def _index(n):
    return pd.bdate_range(end="2026-06-01", periods=n)


def make_df(closes, *, high_off=0.5, low_off=0.5, open_eq_prev=True,
            volume=1_000_000, close_at_high=False, close_at_low=False):
    """Build an OHLCV DataFrame from a list of closes.

    high_off / low_off: how far high/low sit from close (absolute).
    close_at_high / close_at_low: force close to the bar extreme (for CVD).
    """
    n = len(closes)
    closes = [float(c) for c in closes]
    opens = []
    highs = []
    lows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if (open_eq_prev and i > 0) else c
        opens.append(o)
        hi = max(c, o) + high_off
        lo = min(c, o) - low_off
        if close_at_high:
            hi = c + high_off
            c_use = hi  # close pinned to high
        if close_at_low:
            lo = c - low_off
        highs.append(hi)
        lows.append(lo)
    # close-at-extreme handling
    if close_at_high:
        closes = [h for h in highs]
    if close_at_low:
        closes = [l for l in lows]
    if isinstance(volume, (int, float)):
        vols = [int(volume)] * n
    else:
        vols = [int(v) for v in volume]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        },
        index=_index(n),
    )


def uptrend(n=300, start=100.0, step=0.5):
    return make_df([start + step * i for i in range(n)])


def downtrend(n=300, start=250.0, step=0.5):
    return make_df([start - step * i for i in range(n)])


def flat(n=300, level=100.0):
    # tiny alternating jitter so high != low (avoids divide-by-zero in CVD)
    return make_df([level + (0.1 if i % 2 else -0.1) for i in range(n)])


def strong_uptrend(n=60, start=100.0, step=2.0):
    # big step vs small wick offset so the CLOSE clears the prior channel high
    return make_df([start + step * i for i in range(n)], high_off=0.2, low_off=0.2)


def strong_downtrend(n=60, start=250.0, step=2.0):
    return make_df([start - step * i for i in range(n)], high_off=0.2, low_off=0.2)


def grind_up(n=80, start=100.0):
    # mostly up with periodic small pullbacks -> genuine high RSI (avg_loss > 0)
    closes, v = [], float(start)
    for i in range(n):
        v += -0.3 if i % 7 == 6 else 1.0
        closes.append(v)
    return make_df(closes)


def grind_down(n=80, start=250.0):
    closes, v = [], float(start)
    for i in range(n):
        v += 0.3 if i % 7 == 6 else -1.0
        closes.append(v)
    return make_df(closes)


# ===========================================================================
# MovingAverages
# ===========================================================================

def test_ma_uptrend_stack_bullish():
    res = MovingAverages(uptrend()).analyze()
    assert res["stack_bullish"] is True
    assert res["stack_bearish"] is False
    assert res["trend_direction"] == "bullish"
    assert res["price_vs_200"] == "above"
    assert res["ma20"] > res["ma50"] > res["ma200"]


def test_ma_downtrend_stack_bearish():
    res = MovingAverages(downtrend()).analyze()
    assert res["stack_bearish"] is True
    assert res["stack_bullish"] is False
    assert res["trend_direction"] == "bearish"
    assert res["price_vs_200"] == "below"
    assert res["ma20"] < res["ma50"] < res["ma200"]


def test_ma_keys_and_score_range():
    res = MovingAverages(uptrend()).analyze()
    for k in ("ma20", "ma50", "ma200", "close", "stack_bullish", "stack_bearish",
              "price_vs_200", "price_vs_50", "trend_direction",
              "higher_highs_lows", "lower_highs_lows", "score", "score_breakdown"):
        assert k in res
    assert 0 <= res["score"] <= 35
    assert isinstance(res["score_breakdown"], dict)


def test_ma_uptrend_full_score():
    # clean uptrend -> aligned stack + price above + HH/HL = full 35
    res = MovingAverages(uptrend()).analyze()
    assert res["higher_highs_lows"] is True
    assert res["score"] == 35


def test_ma_insufficient_data_empty_result():
    res = MovingAverages(uptrend(n=50)).analyze()
    assert res["score"] == 0
    assert res["ma200"] is None
    assert res["trend_direction"] == "neutral"
    assert res["stack_neutral"] is True


# ===========================================================================
# DonchianChannels
# ===========================================================================

def test_donchian_breakout_up():
    # rising series: last close is a new high above the prior 20-bar channel
    res = DonchianChannels(strong_uptrend()).analyze()
    assert res["breakout_up"] is True
    assert res["breakout_down"] is False
    assert res["close"] > res["upper_band"]
    assert res["score"] == 15


def test_donchian_breakout_down():
    res = DonchianChannels(strong_downtrend()).analyze()
    assert res["breakout_down"] is True
    assert res["breakout_up"] is False
    assert res["close"] < res["lower_band"]
    assert res["score"] == 15


def test_donchian_no_breakout_flat():
    res = DonchianChannels(flat(n=60)).analyze()
    assert res["breakout_up"] is False
    assert res["breakout_down"] is False
    assert res["score"] in (0, 7)


def test_donchian_keys():
    res = DonchianChannels(strong_uptrend()).analyze()
    for k in ("upper_band", "lower_band", "middle_band", "close",
              "breakout_up", "breakout_down", "near_upper", "near_lower",
              "channel_width_pct", "score", "score_breakdown"):
        assert k in res
    assert res["lower_band"] <= res["middle_band"] <= res["upper_band"]


def test_donchian_insufficient_data():
    res = DonchianChannels(uptrend(n=10)).analyze()
    assert res["score"] == 0
    assert res["upper_band"] is None


# ===========================================================================
# VolumeAnalysis
# ===========================================================================

def test_volume_strong_spike_full_score():
    # rvol >= VOLUME_STRONG_MULTIPLIER (1.5) -> full 12 pts
    vols = [1_000_000] * 59 + [5_000_000]  # 5x spike on final bar
    df = make_df([100 + i * 0.1 for i in range(60)], volume=vols)
    res = VolumeAnalysis(df).analyze()
    assert res["rvol"] >= 1.5
    assert res["volume_spike"] is True
    assert res["score"] == 12
    assert res["volume_direction"] == "up"  # rising closes


def test_volume_moderate_tier_partial_score():
    # rvol in [VOLUME_SPIKE_MULTIPLIER 1.2, VOLUME_STRONG_MULTIPLIER 1.5) -> 6 pts
    vols = [1_000_000] * 59 + [1_300_000]  # ~1.3x -> moderate
    df = make_df([100 + i * 0.1 for i in range(60)], volume=vols)
    res = VolumeAnalysis(df).analyze()
    assert 1.2 <= res["rvol"] < 1.5
    assert res["volume_spike"] is True
    assert res["score"] == 6


def test_volume_no_spike_flat_volume():
    df = make_df([100 + i * 0.1 for i in range(60)], volume=1_000_000)
    res = VolumeAnalysis(df).analyze()
    assert res["rvol"] <= 1.2
    assert res["volume_spike"] is False
    assert res["score"] == 0


def test_volume_direction_down():
    # falling closes -> close < open -> direction "down"
    df = make_df([200 - i * 0.5 for i in range(60)], volume=1_000_000)
    res = VolumeAnalysis(df).analyze()
    assert res["volume_direction"] == "down"


def test_volume_keys_and_near_threshold():
    # just below the 1.2 spike threshold -> zero-score band
    vols = [1_000_000] * 59 + [1_150_000]  # ~1.15x -> below 1.2 threshold
    df = make_df([100 + i * 0.1 for i in range(60)], volume=vols)
    res = VolumeAnalysis(df).analyze()
    for k in ("current_volume", "avg_volume", "rvol", "volume_spike",
              "volume_direction", "score", "score_breakdown"):
        assert k in res
    assert res["rvol"] < 1.2
    assert res["volume_spike"] is False
    assert res["score"] == 0


def test_volume_insufficient_data():
    df = make_df([100, 101, 102], volume=1_000_000)
    res = VolumeAnalysis(df).analyze()
    assert res["score"] == 0
    assert res["rvol"] is None


# ===========================================================================
# CVDAnalysis
# ===========================================================================

def test_cvd_bullish_confirmed():
    # rising price + buyers winning each bar (close pinned to high) -> rising CVD
    closes = [100 + i * 0.5 for i in range(40)]
    df = make_df(closes, close_at_high=True, volume=1_000_000)
    res = CVDAnalysis(df, lookback=20).analyze()
    assert res["cvd_slope"] == "rising"
    assert res["cvd_signal"] == "bullish_confirmed"
    assert res["cvd_matches_price"] is True
    assert res["score"] == 12


def test_cvd_bearish_confirmed():
    closes = [200 - i * 0.5 for i in range(40)]
    df = make_df(closes, close_at_low=True, volume=1_000_000)
    res = CVDAnalysis(df, lookback=20).analyze()
    assert res["cvd_slope"] == "falling"
    assert res["cvd_signal"] == "bearish_confirmed"
    assert res["cvd_matches_price"] is True
    assert res["score"] == 12


def test_cvd_keys():
    df = make_df([100 + i * 0.5 for i in range(40)], close_at_high=True)
    res = CVDAnalysis(df, lookback=20).analyze()
    for k in ("cvd_current", "cvd_start", "delta_last_bar", "cvd_slope",
              "cvd_matches_price", "cvd_signal", "score", "score_breakdown"):
        assert k in res


def test_cvd_insufficient_data():
    df = make_df([100 + i for i in range(5)])
    res = CVDAnalysis(df, lookback=20).analyze()
    assert res["score"] == 0
    assert res["cvd_slope"] == "unknown"


# ===========================================================================
# RSIAnalysis
# ===========================================================================

def test_rsi_uptrend_high():
    # mostly-up series with small pullbacks -> RSI pinned very high (>70)
    res = RSIAnalysis(grind_up()).analyze()
    assert res["rsi_current"] > 70


def test_rsi_downtrend_low():
    res = RSIAnalysis(grind_down()).analyze()
    assert res["rsi_current"] < 30


def test_rsi_keys_and_score_range():
    res = RSIAnalysis(grind_up()).analyze()
    for k in ("rsi_current", "rsi_prev", "rsi_trend",
              "bullish_divergence", "bearish_divergence",
              "divergence_strength", "score", "score_breakdown"):
        assert k in res
    assert 0 <= res["score"] <= 12


def test_rsi_no_divergence_clean_trend():
    # a clean trend has no price/RSI disagreement -> no divergence
    res = RSIAnalysis(grind_up()).analyze()
    assert res["bullish_divergence"] is False
    assert res["bearish_divergence"] is False
    assert res["score"] == 0


def test_rsi_insufficient_data():
    res = RSIAnalysis(uptrend(n=10)).analyze()
    assert res["score"] == 0
    assert res["rsi_current"] is None
