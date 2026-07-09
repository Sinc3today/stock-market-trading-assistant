"""tests/test_dipbuy_forward.py -- live forward paper-test of the oversold dip-buy."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _entry_window_open(monkeypatch):
    """Neutralize the 09:45-15:00 ET entry-window guard so open-logic tests don't
    depend on wall-clock time. The guard itself is covered by test_entry_window.py."""
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


def test_config_has_dipbuy_forward_flags():
    import config
    assert config.DIPBUY_FORWARD_ENABLED is True
    assert config.DIPBUY_FORWARD_DTE == 21
    assert config.DIPBUY_FORWARD_TARGET_PCT == 0.50
    assert config.DIPBUY_FORWARD_MAX_HOLD_TD == 10


# ── trigger + entry ──────────────────────────────────────────

def _declining_df(n=60):
    closes = list(range(460, 460 - n, -1))   # steady decline → RSI<30
    idx = pd.bdate_range("2026-01-02", periods=n)
    return pd.DataFrame({"close": [float(c) for c in closes]}, index=idx)


class _FakeLayer:
    def analyze(self, *a, **k):
        return {"strategy": "bull_debit",
                "legs": [{"action": "BUY", "type": "call", "strike": 450},
                         {"action": "SELL", "type": "call", "strike": 460}],
                "entry_price": 4.0, "max_profit": 600.0, "max_loss": 400.0}


class _FakeRec:
    def __init__(self): self.entries = []
    def log_entry(self, **kw): self.entries.append(kw); return "TID123"
    def get_all_trades(self): return []
    def get_open_trades(self): return []
    def _save(self, t): pass


def test_is_fresh_oversold_returns_bool():
    from learning.dipbuy_forward import is_fresh_oversold
    assert isinstance(is_fresh_oversold(_declining_df()), bool)


def test_is_fresh_breakdown_fires_on_new_low():
    from learning.dipbuy_forward import is_fresh_breakdown
    closes = [100.0] * 55 + [95.0]   # fresh close below the prior 50d low
    idx = pd.bdate_range("2026-01-02", periods=len(closes))
    df = pd.DataFrame({"close": closes}, index=idx)
    assert is_fresh_breakdown(df, window=50) is True


def test_dip_signal_priority_and_none(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda d: True)
    monkeypatch.setattr(df_mod, "is_fresh_breakdown", lambda d, window=None: True)
    assert df_mod.dip_signal(_declining_df()) == "oversold"   # oversold priority
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda d: False)
    assert df_mod.dip_signal(_declining_df()) == "breakdown"
    monkeypatch.setattr(df_mod, "is_fresh_breakdown", lambda d, window=None: False)
    assert df_mod.dip_signal(_declining_df()) is None


def test_maybe_open_records_one_candidate_on_trigger(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "dip_signal", lambda d: "oversold")
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out and out["recorded"] is True
    assert len(rec.entries) == 1
    e = rec.entries[0]
    assert e["book"] == "candidate" and e["size"] == 1 and e["dte_bucket"] == "dipbuy"


def test_maybe_open_records_on_breakdown_trigger(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "dip_signal", lambda d: "breakdown")
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out and out["recorded"] is True
    assert "breakdown" in rec.entries[0]["notes"]   # trigger recorded in the note


def test_maybe_open_noop_when_not_triggered(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "dip_signal", lambda d: None)
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out is None and rec.entries == []


def test_maybe_open_noop_when_disabled(monkeypatch):
    import config
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(config, "DIPBUY_FORWARD_ENABLED", False)
    monkeypatch.setattr(df_mod, "dip_signal", lambda d: "oversold")
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out is None and rec.entries == []


# ── resolver ─────────────────────────────────────────────────

_LEGS = [{"action": "BUY", "type": "call", "strike": 450},
         {"action": "SELL", "type": "call", "strike": 460}]


def test_mark_spread_bull_debit_value_rises_with_spot():
    from learning.dipbuy_forward import _mark_spread
    low  = _mark_spread(_LEGS, spot=448.0, vix=20.0, dte_days=10)
    high = _mark_spread(_LEGS, spot=458.0, vix=20.0, dte_days=10)
    assert high > low and high >= 0.0


class _ResRec:
    def __init__(self, trades):
        self._trades = trades
        self.closed = []
    def get_all_trades(self): return self._trades
    def log_exit(self, tid, exit_price, notes="", exit_reason=None):
        self.closed.append((tid, exit_price, exit_reason)); return True
    def _save(self, t): pass


def test_resolve_closes_at_target():
    from learning import dipbuy_forward as df_mod
    rec = _ResRec([{"trade_id": "T1", "book": "candidate", "entry_price": 4.0,
                    "size": 1, "max_profit": 600.0, "td_held": 0, "legs": _LEGS}])
    # spot well above the short strike (460) → spread near max value → 50% target hit
    out = df_mod.resolve_candidates(rec, spy_close=485.0, vix=18.0,
                                    today=pd.Timestamp("2026-03-20").date())
    assert len(out) == 1 and rec.closed and rec.closed[0][2] == "target"


def test_resolve_closes_at_max_hold():
    from learning import dipbuy_forward as df_mod
    rec = _ResRec([{"trade_id": "T2", "book": "candidate", "entry_price": 4.0,
                    "size": 1, "max_profit": 600.0, "td_held": 9, "legs": _LEGS}])
    out = df_mod.resolve_candidates(rec, spy_close=451.0, vix=18.0,
                                    today=pd.Timestamp("2026-03-20").date())
    assert rec.closed and rec.closed[0][2] == "time_stop"


def test_resolve_leaves_running_trade_open():
    from learning import dipbuy_forward as df_mod
    rec = _ResRec([{"trade_id": "T3", "book": "candidate", "entry_price": 4.0,
                    "size": 1, "max_profit": 600.0, "td_held": 2, "legs": _LEGS}])
    out = df_mod.resolve_candidates(rec, spy_close=451.0, vix=18.0,
                                    today=pd.Timestamp("2026-03-20").date())
    assert out == [] and rec.closed == []


def test_resolve_ignores_non_candidate_books():
    from learning import dipbuy_forward as df_mod
    rec = _ResRec([{"trade_id": "D", "book": "disciplined", "outcome": "open"}])
    out = df_mod.resolve_candidates(rec, spy_close=450.0, vix=18.0,
                                    today=pd.Timestamp("2026-03-20").date())
    assert out == [] and rec.closed == []


# ── Multi-instrument (QQQ added 2026-07-09, docs/DIPBUY_MULTI_INSTRUMENT.md) ──

def test_qqq_candidate_records_with_own_ticker(tmp_path, monkeypatch):
    import config
    import pandas as pd
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)
    from journal.trade_recorder import TradeRecorder
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "dip_signal", lambda d: "oversold")
    df = _declining_df()

    class _OL:
        def analyze(self, ticker, *a, **k):
            assert ticker == "QQQ"                    # layer gets the right ticker
            return {"strategy": "bull_debit", "entry_price": 2.0,
                    "legs": [{"action": "BUY", "type": "call", "strike": 440},
                             {"action": "SELL", "type": "call", "strike": 451}],
                    "max_profit": 900, "max_loss": 200}

    r = df_mod.maybe_open_dipbuy(df, spot=440.0, ivr=30, options_layer=_OL(),
                                 recorder=TradeRecorder(), ticker="QQQ")
    assert r and r["recorded"]
    t = TradeRecorder().get_open_trades()[0]
    assert t["ticker"] == "QQQ" and t["book"] == config.DIPBUY_FORWARD_BOOK


def test_per_ticker_idempotency_spy_and_qqq_both_open(tmp_path, monkeypatch):
    import config
    import pandas as pd
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)
    from journal.trade_recorder import TradeRecorder
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "dip_signal", lambda d: "oversold")
    df = _declining_df()

    class _OL:
        def analyze(self, ticker, *a, **k):
            return {"strategy": "bull_debit", "entry_price": 2.0,
                    "legs": [{"action": "BUY", "type": "call", "strike": 440}],
                    "max_profit": 900, "max_loss": 200}

    rec = TradeRecorder()
    assert df_mod.maybe_open_dipbuy(df, spot=440, ivr=30, options_layer=_OL(),
                                    recorder=rec, ticker="SPY")
    # a same-day QQQ candidate must NOT be blocked by the SPY one...
    assert df_mod.maybe_open_dipbuy(df, spot=440, ivr=30, options_layer=_OL(),
                                    recorder=rec, ticker="QQQ")
    # ...but a second QQQ the same day is
    assert df_mod.maybe_open_dipbuy(df, spot=440, ivr=30, options_layer=_OL(),
                                    recorder=rec, ticker="QQQ") is None


def test_resolver_marks_non_spy_at_own_spot(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    from learning.dipbuy_forward import resolve_candidates
    rec = TradeRecorder()
    rec.log_entry(ticker="QQQ", entry_price=2.0, size=1, trade_type="debit_spread",
                  strategy="debit_spread", direction="bullish",
                  legs=[{"action": "BUY", "type": "call", "strike": 440},
                        {"action": "SELL", "type": "call", "strike": 451}],
                  max_profit=900, book=config.DIPBUY_FORWARD_BOOK)
    # QQQ deep ITM at ITS spot (SPY close would mis-mark badly) -> target close
    closed = resolve_candidates(rec, spy_close=628.0, vix=18.0,
                                closes={"QQQ": 470.0})
    assert len(closed) == 1
