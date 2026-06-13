"""
Synthetic (no-network) unit tests for signals/alert_builder.AlertBuilder.

Characterization tests: assertions match the module's ACTUAL output as observed
by running the code, not guessed. No live clients, no network, no sleeps.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.alert_builder import AlertBuilder  # noqa: E402


# ─────────────────────────────────────────
# Synthetic input fixtures (plain dicts/values)
# ─────────────────────────────────────────

def _score_result():
    return {
        "direction": "bullish",
        "final_score": 82,
        "raw_score": 78,
        "tier": "high_conviction",
        "alert_emoji": "🔥",
        "confluence_applied": True,
        "layer_scores": {
            "trend":  {"score": 30, "max": 35},
            "setup":  {"score": 28, "max": 35},
            "volume": {"score": 24, "max": 30},
        },
        "indicator_scores": {"rsi": 10, "donchian": 12},
    }


def _gate_data():
    return {"rr_ratio": 2.5, "earnings_date": "2026-07-15"}


def _ma_result():
    return {
        "ma20": 101.5,
        "ma50": 99.2,
        "ma200": 95.0,
        "stack_bullish": True,
        "higher_highs_lows": True,
    }


def _donchian_result():
    return {"breakout_up": True}


def _volume_result():
    return {"rvol": 1.8}


def _cvd_result():
    return {"cvd_slope": 0.45}


def _rsi_result():
    return {
        "rsi_current": 61.2,
        "bullish_divergence": True,
        "divergence_strength": "strong",
    }


def _build_alert(**overrides):
    builder = AlertBuilder()
    kwargs = dict(
        ticker="AAPL",
        timeframe="day",
        mode="swing",
        score_result=_score_result(),
        gate_data=_gate_data(),
        ma_result=_ma_result(),
        donchian_result=_donchian_result(),
        volume_result=_volume_result(),
        cvd_result=_cvd_result(),
        rsi_result=_rsi_result(),
        entry=100.123,
        stop=97.456,
        target=110.789,
        exit_type="trail_stop",
        confluence_timeframes=["day", "4hour"],
    )
    kwargs.update(overrides)
    return builder.build(**kwargs)


# ─────────────────────────────────────────
# build() — alert dict construction
# ─────────────────────────────────────────

def test_build_returns_expected_identity_and_score_keys():
    alert = _build_alert()

    # Identity: direction uppercased, mode capitalized
    assert alert["ticker"] == "AAPL"
    assert alert["direction"] == "BULLISH"
    assert alert["mode"] == "Swing"
    assert alert["timeframe"] == "day"
    assert alert["tier"] == "high_conviction"
    assert alert["emoji"] == "🔥"

    # Score block
    assert alert["final_score"] == 82
    assert alert["raw_score"] == 78
    assert alert["confluence"] is True
    assert alert["confluence_timeframes"] == ["day", "4hour"]
    assert alert["instrument"] == "stock"


def test_build_rounds_trade_levels_and_rr():
    alert = _build_alert()

    # entry/stop/target rounded to 2dp
    assert alert["entry"] == 100.12
    assert alert["stop"] == 97.46
    assert alert["target"] == 110.79
    assert alert["rr_ratio"] == 2.5

    # exit_type mapped to its human label
    assert alert["exit_type"] == "Trail Stop (MA-based)"


def test_build_copies_indicator_snapshots():
    alert = _build_alert()
    assert alert["ma20"] == 101.5
    assert alert["ma50"] == 99.2
    assert alert["ma200"] == 95.0
    assert alert["rsi"] == 61.2
    assert alert["rvol"] == 1.8
    assert alert["cvd_slope"] == 0.45
    assert alert["earnings_date"] == "2026-07-15"


def test_build_setup_tags_from_indicators():
    alert = _build_alert()
    tags = alert["setup_tags"]
    # Donchian breakout up + RSI bullish divergence + MA stack + HH/HL
    assert "✅ Donchian breakout UP" in tags
    assert "✅ RSI bullish divergence (strong)" in tags
    assert "✅ MA stack bullish" in tags
    assert "✅ HH/HL structure" in tags
    assert len(tags) == 4


def test_build_defaults_when_results_empty():
    # Empty indicator/score/gate dicts should not crash; defaults applied.
    builder = AlertBuilder()
    alert = builder.build(
        ticker="SPY",
        timeframe="day",
        mode="intraday",
        score_result={},
        gate_data={},
        ma_result={},
        donchian_result={},
        volume_result={},
        cvd_result={},
        rsi_result={},
        entry=400.0,
        stop=395.0,
        target=410.0,
        exit_type="weird_type",
    )
    assert alert["direction"] == "NEUTRAL"
    assert alert["final_score"] == 0
    assert alert["tier"] == "none"
    assert alert["setup_tags"] == []
    # confluence_timeframes defaults to [timeframe]
    assert alert["confluence_timeframes"] == ["day"]
    # unknown exit_type passes through unchanged
    assert alert["exit_type"] == "weird_type"
    # MA snapshots are None when absent
    assert alert["ma20"] is None


# ─────────────────────────────────────────
# format_discord_message()
# ─────────────────────────────────────────

def test_format_discord_message_contains_core_fields():
    alert = _build_alert()
    msg = AlertBuilder().format_discord_message(alert)

    assert isinstance(msg, str)
    assert "HIGH CONVICTION ALERT" in msg
    assert "**Ticker:**     AAPL" in msg
    assert "**Mode:**       Swing Trade" in msg
    assert "**Direction:**  BULLISH 📈" in msg
    assert "**Score:**      82 / 100" in msg

    # Trade levels rendered with $ prefix
    assert "Entry:   $100.12" in msg
    assert "Stop:    $97.46" in msg
    assert "Target:  $110.79" in msg
    assert "R/R:     2.5 : 1" in msg
    assert "Exit:    Trail Stop (MA-based)" in msg


def test_format_discord_message_breakdown_and_confluence():
    alert = _build_alert()
    msg = AlertBuilder().format_discord_message(alert)

    # Score breakdown uses layer_scores values
    assert "Trend:   30/35" in msg
    assert "Setup:   28/35" in msg
    assert "Volume:  24/30" in msg

    # Multi-timeframe confluence note
    assert "⏱ Confirmed on: day + 4hour" in msg

    # Indicator line
    assert "RSI: 61.2" in msg
    assert "RVOL: 1.8x" in msg


def test_format_discord_message_standard_tier_and_single_tf():
    # tier != high_conviction -> STANDARD ALERT; single tf -> "Timeframe:" note;
    # bearish direction -> 📉
    alert = _build_alert(
        score_result={
            **_score_result(),
            "tier": "standard",
            "direction": "bearish",
        },
        confluence_timeframes=["day"],
    )
    msg = AlertBuilder().format_discord_message(alert)

    assert "STANDARD ALERT" in msg
    assert "HIGH CONVICTION ALERT" not in msg
    assert "**Direction:**  BEARISH 📉" in msg
    assert "⏱ Timeframe: day" in msg
    assert "Confirmed on" not in msg
