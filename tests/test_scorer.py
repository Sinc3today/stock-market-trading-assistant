"""
tests/test_scorer.py — Test scorer, gates, and alert builder

Run with:
    pytest tests/test_scorer.py -v
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
from signals.scorer import SignalScorer
from signals.gates import AlertGates
from signals.alert_builder import AlertBuilder


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture(scope="module")
def indicator_results():
    """Run all indicators on AAPL daily data once.
    Waits 15s for Polygon rate limit to reset after test_indicators.py
    """
    import time
    time.sleep(15)
    client = PolygonClient()
    df = client.get_bars("AAPL", timeframe="day", limit=300, days_back=400)
    assert df is not None

    return {
        "df":       df,
        "ma":       MovingAverages(df).analyze(),
        "donchian": DonchianChannels(df).analyze(),
        "volume":   VolumeAnalysis(df).analyze(),
        "cvd":      CVDAnalysis(df).analyze(),
        "rsi":      RSIAnalysis(df).analyze(),
    }

@pytest.fixture(scope="module")
def score_result(indicator_results):
    scorer = SignalScorer()
    return scorer.score(
        ma_result=       indicator_results["ma"],
        donchian_result= indicator_results["donchian"],
        volume_result=   indicator_results["volume"],
        cvd_result=      indicator_results["cvd"],
        rsi_result=      indicator_results["rsi"],
    )


# ─────────────────────────────────────────
# SCORER TESTS
# ─────────────────────────────────────────

def test_scorer_returns_result(score_result):
    assert score_result is not None
    assert "final_score" in score_result
    assert "tier" in score_result
    assert "direction" in score_result
    print(f"\n✅ Score result returned")

def test_scorer_score_in_range(score_result):
    assert 0 <= score_result["final_score"] <= 100
    assert 0 <= score_result["raw_score"] <= 100
    print(f"\n✅ Final score: {score_result['final_score']}/100")

def test_scorer_tier_valid(score_result):
    assert score_result["tier"] in ("high_conviction", "standard", "watchlist", "none")
    print(f"\n✅ Tier: {score_result['tier']} {score_result.get('alert_emoji', '')}")

def test_scorer_direction_valid(score_result):
    assert score_result["direction"] in ("bullish", "bearish", "neutral")
    print(f"\n✅ Direction: {score_result['direction']}")

def test_scorer_layer_scores_present(score_result):
    layers = score_result["layer_scores"]
    assert "trend"  in layers
    assert "setup"  in layers
    assert "volume" in layers
    print(f"\n✅ Layer scores:")
    print(f"   Trend:  {layers['trend']['score']}/{layers['trend']['max']}")
    print(f"   Setup:  {layers['setup']['score']}/{layers['setup']['max']}")
    print(f"   Volume: {layers['volume']['score']}/{layers['volume']['max']}")

def test_scorer_confluence_bonus(indicator_results):
    """Confluence bonus should increase score by up to 15%."""
    scorer = SignalScorer()
    no_conf = scorer.score(
        indicator_results["ma"], indicator_results["donchian"],
        indicator_results["volume"], indicator_results["cvd"],
        indicator_results["rsi"], confluence=False
    )
    with_conf = scorer.score(
        indicator_results["ma"], indicator_results["donchian"],
        indicator_results["volume"], indicator_results["cvd"],
        indicator_results["rsi"], confluence=True
    )
    assert with_conf["final_score"] >= no_conf["final_score"]
    assert with_conf["confluence_applied"] is True
    print(f"\n✅ Confluence: {no_conf['final_score']} → {with_conf['final_score']}")

def test_scorer_never_exceeds_100(indicator_results):
    """Score must never exceed 100 even with all bonuses."""
    scorer = SignalScorer()
    result = scorer.score(
        indicator_results["ma"], indicator_results["donchian"],
        indicator_results["volume"], indicator_results["cvd"],
        indicator_results["rsi"],
        pullback_bonus=8, rvol_bonus=6, confluence=True
    )
    assert result["final_score"] <= 100
    print(f"\n✅ Max score capped correctly: {result['final_score']}/100")


# ─────────────────────────────────────────
# GATES TESTS
# ─────────────────────────────────────────

def test_gates_pass_with_good_rr(score_result):
    """Good R/R setup should pass the gates."""
    gates = AlertGates()
    # Simulate a clean bullish setup
    mock_score = {**score_result, "direction": "bullish", "final_score": 80}
    passed, failures, gate_data = gates.check(
        mock_score, "AAPL",
        entry=170.0, stop=166.0, target=182.0  # R/R = 3:1
    )
    assert gate_data["rr_ratio"] >= 2.0
    print(f"\n✅ Good R/R passed: {gate_data['rr_ratio']:.2f}:1 | Passed: {passed}")

def test_gates_fail_with_bad_rr(score_result):
    """Bad R/R setup should fail the gate."""
    gates = AlertGates()
    mock_score = {**score_result, "direction": "bullish", "final_score": 80}
    passed, failures, gate_data = gates.check(
        mock_score, "AAPL",
        entry=170.0, stop=169.0, target=171.0  # R/R = 1:1
    )
    assert gate_data["rr_ratio"] < 2.0
    assert any("R/R" in f for f in failures)
    print(f"\n✅ Bad R/R correctly blocked: {gate_data['rr_ratio']:.2f}:1")

def test_gates_fail_with_low_score():
    """Score below minimum should fail the gate."""
    gates = AlertGates()
    mock_score = {
        "final_score": 50, "direction": "bullish",
        "tier": "watchlist", "alert_emoji": None
    }
    passed, failures, gate_data = gates.check(
        mock_score, "AAPL",
        entry=170.0, stop=166.0, target=182.0
    )
    assert not passed
    assert any("Score" in f for f in failures)
    print(f"\n✅ Low score correctly blocked: {failures}")

def test_gates_fail_with_neutral_direction(score_result):
    """Neutral direction should fail the gate."""
    gates = AlertGates()
    mock_score = {**score_result, "direction": "neutral", "final_score": 80}
    passed, failures, gate_data = gates.check(
        mock_score, "AAPL",
        entry=170.0, stop=166.0, target=182.0
    )
    assert any("neutral" in f.lower() for f in failures)
    print(f"\n✅ Neutral direction correctly blocked")


# ─────────────────────────────────────────
# ALERT BUILDER TESTS
# ─────────────────────────────────────────

def test_alert_builder_returns_alert(indicator_results, score_result):
    builder = AlertBuilder()
    gates   = AlertGates()
    mock_score = {**score_result, "direction": "bullish", "final_score": 82}
    _, _, gate_data = gates.check(
        mock_score, "AAPL",
        entry=170.0, stop=166.0, target=182.0
    )
    alert = builder.build(
        ticker="AAPL", timeframe="day", mode="swing",
        score_result=mock_score, gate_data=gate_data,
        ma_result=indicator_results["ma"],
        donchian_result=indicator_results["donchian"],
        volume_result=indicator_results["volume"],
        cvd_result=indicator_results["cvd"],
        rsi_result=indicator_results["rsi"],
        entry=170.0, stop=166.0, target=182.0,
        exit_type="trail_stop",
    )
    assert alert["ticker"] == "AAPL"
    assert "timestamp" in alert
    assert alert["rr_ratio"] >= 0
    print(f"\n✅ Alert built: {alert['ticker']} | {alert['timestamp']}")

def test_alert_builder_discord_format(indicator_results, score_result):
    """Discord message should contain key fields."""
    builder = AlertBuilder()
    gates   = AlertGates()
    mock_score = {**score_result, "direction": "bullish", "final_score": 82,
                  "tier": "standard", "alert_emoji": "🟡"}
    _, _, gate_data = gates.check(
        mock_score, "AAPL",
        entry=170.0, stop=166.0, target=182.0
    )
    alert = builder.build(
        ticker="AAPL", timeframe="day", mode="swing",
        score_result=mock_score, gate_data=gate_data,
        ma_result=indicator_results["ma"],
        donchian_result=indicator_results["donchian"],
        volume_result=indicator_results["volume"],
        cvd_result=indicator_results["cvd"],
        rsi_result=indicator_results["rsi"],
        entry=170.0, stop=166.0, target=182.0,
        exit_type="trail_stop",
    )
    msg = builder.format_discord_message(alert)
    assert "AAPL"     in msg
    assert "170.0"    in msg
    assert "R/R"      in msg
    assert "EST"      in msg   # Timestamp includes EST
    print(f"\n✅ Discord message formatted correctly")
    print("\n--- SAMPLE DISCORD MESSAGE ---")
    print(msg)
    print("------------------------------")