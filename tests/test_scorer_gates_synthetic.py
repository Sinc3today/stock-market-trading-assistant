"""
tests/test_scorer_gates_synthetic.py — Fast, no-network unit tests for the
signal scorer and alert gates.

These exercise signals/scorer.py (SignalScorer) and signals/gates.py
(AlertGates) using SYNTHETIC plain-dict indicator results. No live Polygon
calls, no sleeps, no real EarningsCalendar fetches — a fake calendar is
injected so AlertGates never touches the network or disk cache.

Characterization style: assertions were matched to ACTUAL observed output,
not guessed. Where exact point totals matter they are asserted against the
real scoring arithmetic in scorer.py.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from signals.scorer import SignalScorer
from signals.gates import AlertGates


# ─────────────────────────────────────────────────────────────────────────
# Synthetic indicator-result builders
#
# The scorer reads only a handful of keys from each indicator result:
#   ma_result:       "score", "trend_direction"
#   donchian_result: "score", "breakout_up", "near_upper", "breakout_down",
#                    "near_lower"
#   volume_result:   "score"
#   cvd_result:      "score"
#   rsi_result:      "score", "bullish_divergence", "bearish_divergence"
# Anything else in a real indicator result is irrelevant to scoring.
# ─────────────────────────────────────────────────────────────────────────


def _bullish_inputs():
    """Clearly bullish, near-max scores across every layer."""
    ma = {"score": 35, "trend_direction": "bullish"}
    don = {"score": 15, "breakout_up": True, "near_upper": True,
           "breakout_down": False, "near_lower": False}
    vol = {"score": 12}
    cvd = {"score": 12}
    rsi = {"score": 12, "bullish_divergence": True, "bearish_divergence": False}
    return ma, don, vol, cvd, rsi


def _bearish_inputs():
    """Clearly bearish, high scores but downward bias."""
    ma = {"score": 35, "trend_direction": "bearish"}
    don = {"score": 15, "breakout_up": False, "near_upper": False,
           "breakout_down": True, "near_lower": True}
    vol = {"score": 12}
    cvd = {"score": 12}
    rsi = {"score": 12, "bullish_divergence": False, "bearish_divergence": True}
    return ma, don, vol, cvd, rsi


def _neutral_low_inputs():
    """Low scores, no directional consensus."""
    ma = {"score": 5, "trend_direction": "neutral"}
    don = {"score": 0, "breakout_up": False, "near_upper": False,
           "breakout_down": False, "near_lower": False}
    vol = {"score": 2}
    cvd = {"score": 0}
    rsi = {"score": 3, "bullish_divergence": False, "bearish_divergence": False}
    return ma, don, vol, cvd, rsi


class _FakeEarningsCalendar:
    """Stand-in for EarningsCalendar — returns a canned entry per ticker.

    get_for_ticker(ticker, days) mirrors the real signature. Returns a dict
    shaped like the real cache entry ({"earnings_date", "days_away"}) or None
    when the ticker has no scheduled earnings in the block window.
    """

    def __init__(self, entries=None):
        self._entries = entries or {}

    def get_for_ticker(self, ticker, days=30):
        return self._entries.get(ticker)


def _gates_no_earnings():
    """AlertGates wired with a calendar that reports no earnings for any ticker."""
    return AlertGates(earnings_calendar=_FakeEarningsCalendar())


# ─────────────────────────────────────────────────────────────────────────
# SignalScorer tests
# ─────────────────────────────────────────────────────────────────────────


def test_scorer_bullish_is_high_and_bullish():
    scorer = SignalScorer()
    result = scorer.score(*_bullish_inputs())

    # Max possible without bonuses: 35 (trend) + 15+12 (setup) + 12+12 (volume)
    assert result["raw_score"] == 35 + 15 + 12 + 12 + 12
    assert result["final_score"] == result["raw_score"]
    assert 0 <= result["final_score"] <= 100
    assert result["direction"] == "bullish"
    # 86 >= SCORE_HIGH_CONVICTION (68)
    assert result["tier"] == "high_conviction"
    assert result["alert_emoji"] == "🔴"
    assert result["confluence_applied"] is False


def test_scorer_bearish_direction_and_high_tier():
    scorer = SignalScorer()
    result = scorer.score(*_bearish_inputs())

    assert result["direction"] == "bearish"
    assert 0 <= result["final_score"] <= 100
    assert result["final_score"] >= config.SCORE_HIGH_CONVICTION
    assert result["tier"] == "high_conviction"


def test_scorer_neutral_low_is_low_and_neutral():
    scorer = SignalScorer()
    result = scorer.score(*_neutral_low_inputs())

    # 5 + 0 + 2 + 0 + 3 = 10
    assert result["raw_score"] == 10
    assert result["final_score"] == 10
    assert result["direction"] == "neutral"
    # 10 < SCORE_WATCHLIST (30) → "none"
    assert result["tier"] == "none"
    assert result["alert_emoji"] is None


def test_scorer_bonuses_and_caps():
    scorer = SignalScorer()
    # pullback capped at 8, rvol capped at 6 even when over-supplied.
    result = scorer.score(*_bullish_inputs(), pullback_bonus=20, rvol_bonus=20)

    # raw = 86 (base) + 8 (pullback cap) + 6 (rvol cap) = 100 (capped at 100)
    assert result["raw_score"] == 100
    assert result["indicator_scores"]["pullback"]["score"] == 8
    assert result["indicator_scores"]["rvol_bonus"]["score"] == 6
    assert result["final_score"] == 100


def test_scorer_confluence_caps_at_100():
    scorer = SignalScorer()
    # Mid-range base so the 1.15x multiplier is visible but stays <= 100.
    ma = {"score": 20, "trend_direction": "bullish"}
    don = {"score": 10, "breakout_up": True, "near_upper": False,
           "breakout_down": False, "near_lower": False}
    vol = {"score": 6}
    cvd = {"score": 6}
    rsi = {"score": 6, "bullish_divergence": False, "bearish_divergence": False}

    base = scorer.score(ma, don, vol, cvd, rsi)
    boosted = scorer.score(ma, don, vol, cvd, rsi, confluence=True)

    assert boosted["confluence_applied"] is True
    assert boosted["final_score"] == min(
        round(base["raw_score"] * config.CONFLUENCE_BONUS_MULTIPLIER), 100
    )
    assert boosted["final_score"] > base["final_score"]
    assert boosted["final_score"] <= 100


def test_scorer_layer_breakdown_sums():
    scorer = SignalScorer()
    result = scorer.score(*_bullish_inputs())
    layers = result["layer_scores"]
    total = layers["trend"]["score"] + layers["setup"]["score"] + layers["volume"]["score"]
    # Layer scores must reconcile to raw_score (no confluence here).
    assert total == result["raw_score"]
    assert result["direction"] in ("bullish", "bearish", "neutral")


# ─────────────────────────────────────────────────────────────────────────
# AlertGates tests
# ─────────────────────────────────────────────────────────────────────────


def test_gates_all_pass():
    gates = _gates_no_earnings()
    score_result = {"final_score": 80, "direction": "bullish"}
    # Bullish: target above entry, stop below. R/R = (110-100)/(100-95) = 2.0
    passed, failures, gate_data = gates.check(
        score_result, "AAPL", entry=100.0, stop=95.0, target=110.0
    )
    assert passed is True
    assert failures == []
    assert gate_data["rr_ratio"] == 2.0


def test_gates_fail_low_score():
    gates = _gates_no_earnings()
    score_result = {"final_score": 10, "direction": "bullish"}
    passed, failures, _ = gates.check(
        score_result, "AAPL", entry=100.0, stop=95.0, target=110.0
    )
    assert passed is False
    assert any("Score too low" in f for f in failures)


def test_gates_fail_neutral_direction():
    gates = _gates_no_earnings()
    score_result = {"final_score": 80, "direction": "neutral"}
    passed, failures, _ = gates.check(
        score_result, "AAPL", entry=100.0, stop=95.0, target=110.0
    )
    assert passed is False
    assert any("neutral" in f.lower() for f in failures)


def test_gates_fail_bad_risk_reward():
    gates = _gates_no_earnings()
    score_result = {"final_score": 80, "direction": "bullish"}
    # R/R = (101-100)/(100-95) = 0.2 → below MIN_RISK_REWARD_RATIO (1.5)
    passed, failures, gate_data = gates.check(
        score_result, "AAPL", entry=100.0, stop=95.0, target=101.0
    )
    assert passed is False
    assert any("R/R too low" in f for f in failures)
    assert gate_data["rr_ratio"] < config.MIN_RISK_REWARD_RATIO


def test_gates_fail_invalid_stop():
    gates = _gates_no_earnings()
    score_result = {"final_score": 80, "direction": "bullish"}
    # Stop above entry on a bullish trade → risk <= 0 → invalid.
    passed, failures, _ = gates.check(
        score_result, "AAPL", entry=100.0, stop=105.0, target=110.0
    )
    assert passed is False
    assert any("Invalid stop" in f for f in failures)


def test_gates_fail_earnings_conflict():
    cal = _FakeEarningsCalendar(
        {"AAPL": {"earnings_date": "2026-06-13", "days_away": 1}}
    )
    gates = AlertGates(earnings_calendar=cal)
    score_result = {"final_score": 80, "direction": "bullish"}
    # days_away (1) <= EARNINGS_BLOCK_DAYS (2) → suppressed.
    passed, failures, gate_data = gates.check(
        score_result, "AAPL", entry=100.0, stop=95.0, target=110.0
    )
    assert passed is False
    assert any("Earnings within" in f for f in failures)
    assert gate_data["earnings_date"] == "2026-06-13"
