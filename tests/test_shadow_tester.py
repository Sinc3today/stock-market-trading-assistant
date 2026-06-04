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


# ── Task 3: shadow_stats ──────────────────────────────────────────────────────

def test_shadow_stats_aggregates_shadow_book(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    from learning.shadow_tester import shadow_stats
    rec = TradeRecorder()
    # two closed shadow trades (1 win, 1 loss) + one disciplined (ignored)
    rec.log_entry(ticker="SPY", entry_price=1.2, size=1, trade_type="credit_spread",
                  strategy="credit_spread", book="shadow", source="auto-paper",
                  legs=[{"action": "SELL", "type": "put", "strike": 755}])
    rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="credit_spread",
                  strategy="credit_spread", book="shadow", source="auto-paper",
                  legs=[{"action": "SELL", "type": "put", "strike": 750}])
    rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="iron_condor",
                  strategy="iron_condor", book="disciplined", source="auto-paper")
    # close the two shadow trades with known P&L + stamp directional
    trades = rec.get_all_trades()
    sh = [t for t in trades if t["book"] == "shadow"]
    sh[0]["outcome"] = "win";  sh[0]["pnl_dollars"] = 80.0;  sh[0]["shadow_directional"] = "correct"
    sh[1]["outcome"] = "loss"; sh[1]["pnl_dollars"] = -40.0; sh[1]["shadow_directional"] = "wrong"
    rec._save(trades)

    s = shadow_stats(n_days=3650, trade_recorder=rec)
    assert s["n"] == 2
    assert s["closed_pnl"] == 40.0                 # 80 - 40
    assert s["directional_win_rate"] == 0.5        # 1 of 2 correct


def test_shadow_stats_empty_is_neutral(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    from learning.shadow_tester import shadow_stats
    s = shadow_stats(n_days=30, trade_recorder=TradeRecorder())
    assert s["n"] == 0 and s["closed_pnl"] == 0.0 and s["directional_win_rate"] == 0.0
