import os, sys
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config
from signals.spy_daily_strategy import SPYDailyStrategy
from signals.regime_detector import RegimeResult, Regime


def _tradeable_regime():
    return RegimeResult(
        regime=Regime.TRENDING_UP_CALM, tradeable=True, play="Bull debit spread",
        confidence=0.8, reasons=["trend intact"],
        metrics={"spy_close": 742.6, "ma200": 678.0, "ma200_dist_%": 9.4,
                 "adx": 34.0, "vix": 17.0, "ivr": 40.0},
    )


def _strategy_with_stubs():
    strat = SPYDailyStrategy()
    strat._fetch_spy_daily = mock.Mock(return_value=None)
    strat._fetch_vix = mock.Mock(return_value=17.0)
    strat._fetch_ivr = mock.Mock(return_value=40.0)
    strat.detector = mock.Mock()
    strat.detector.classify.return_value = _tradeable_regime()
    return strat


def test_meta_gate_skips_low_prob_trade():
    strat = _strategy_with_stubs()
    with mock.patch.object(config, "META_LABEL_ENABLED", True), \
         mock.patch("signals.spy_daily_strategy.MetaLabeler") as ML:
        ML.return_value.score.return_value = {"prob": 0.40, "tier": "skip", "take": False}
        card = strat.build_today()
    assert card["tradeable"] is False
    assert "meta" in " ".join(card.get("reasons", [])).lower()


def test_meta_gate_tags_tier_when_taken():
    strat = _strategy_with_stubs()
    strat.options = mock.Mock()
    strat.options.analyze.return_value = {"tradeable": True, "strategy": "bull_debit"}
    strat._format_plan = mock.Mock(return_value={})
    strat._format_discord = mock.Mock(return_value="msg")
    with mock.patch.object(config, "META_LABEL_ENABLED", True), \
         mock.patch("signals.spy_daily_strategy.MetaLabeler") as ML:
        ML.return_value.score.return_value = {"prob": 0.80, "tier": "high", "take": True}
        card = strat.build_today()
    assert card["tradeable"] is True
    assert card.get("meta_tier") == "high"


def test_flag_off_is_noop():
    strat = _strategy_with_stubs()
    strat.options = mock.Mock()
    strat.options.analyze.return_value = {"tradeable": True, "strategy": "bull_debit"}
    strat._format_plan = mock.Mock(return_value={})
    strat._format_discord = mock.Mock(return_value="msg")
    with mock.patch.object(config, "META_LABEL_ENABLED", False):
        card = strat.build_today()
    assert card["tradeable"] is True
    assert card.get("meta_tier") in (None, "regime_driven")
