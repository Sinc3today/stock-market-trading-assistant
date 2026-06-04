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


# ── Task 2: run_shadow ────────────────────────────────────────────────────────

def test_run_shadow_records_shadow_trade_on_extension_skip(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "SHADOW_TEST_ENABLED", True)
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", False)
    from datetime import date
    from journal.trade_recorder import TradeRecorder
    from learning.shadow_tester import run_shadow
    from signals.regime_detector import Regime

    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended (wait for pullback)",
                         reasons=[], metrics={"spy_close": 760.0, "ivr": 55.0})

    class _FakeLayer:
        def analyze(self, *a, **k):
            return {"strategy": "credit_spread", "direction": "bullish",
                    "legs": [{"action": "SELL", "type": "put", "strike": 755},
                             {"action": "BUY", "type": "put", "strike": 750}],
                    "max_profit": 120.0, "max_loss": 380.0, "net_premium": 1.2}

    rec = TradeRecorder()
    out = run_shadow(rr, spot=760.0, ivr=55.0, options_layer=_FakeLayer(),
                     trade_recorder=rec, today=date(2026, 6, 3))
    assert out is not None and out["recorded"] is True
    t = [x for x in rec.get_all_trades() if x.get("book") == "shadow"]
    assert len(t) == 1
    assert t[0]["source"] == "auto-paper"
    assert t[0]["strategy"] == "credit_spread"
    assert t[0].get("entry_spy") == 760.0       # recorded for directional scoring


def test_run_shadow_returns_none_when_not_extension_skip():
    from learning.shadow_tester import run_shadow
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=True,
                         recommendation="BULL CALL DEBIT SPREAD", reasons=[], metrics={})
    assert run_shadow(rr, spot=760.0, ivr=55.0, options_layer=object(),
                      trade_recorder=object()) is None


def test_run_shadow_disabled_returns_none(monkeypatch):
    import config
    monkeypatch.setattr(config, "SHADOW_TEST_ENABLED", False)
    from learning.shadow_tester import run_shadow
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended", reasons=[], metrics={})
    assert run_shadow(rr, spot=760.0, ivr=55.0, options_layer=object(),
                      trade_recorder=object()) is None
