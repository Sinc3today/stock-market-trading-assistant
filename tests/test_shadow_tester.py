import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from types import SimpleNamespace
from signals.regime_detector import Regime
from learning.shadow_tester import _is_extension_skip


def _rr(regime, tradeable, recommendation):
    return SimpleNamespace(regime=regime, tradeable=tradeable, recommendation=recommendation, reasons=[], metrics={})


def test_extension_skip_detected():
    rr = _rr(Regime.TRENDING_UP_CALM, False, "SKIP — trend too extended (wait for pullback)")
    assert _is_extension_skip(rr) is True


def test_tradeable_day_not_extension_skip():
    rr = _rr(Regime.TRENDING_UP_CALM, True, "BULL CALL DEBIT SPREAD — buy the directional move")
    assert _is_extension_skip(rr) is False


def test_other_skip_reason_not_extension_skip():
    rr = _rr(Regime.UNKNOWN, False, "SKIP — SPY too close to 200MA, direction unclear")
    assert _is_extension_skip(rr) is False


def test_extension_skip_detected_via_play_field():
    """Covers the REAL RegimeResult shape (.play, no .recommendation) — guards
    the production path so the feature can't silently no-op live."""
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         play="SKIP — trend too extended (wait for pullback)",
                         reasons=[], metrics={})
    assert _is_extension_skip(rr) is True
