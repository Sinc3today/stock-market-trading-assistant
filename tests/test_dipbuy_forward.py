"""tests/test_dipbuy_forward.py -- live forward paper-test of the oversold dip-buy."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


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


def test_maybe_open_records_one_candidate_on_trigger(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda d: True)
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out and out["recorded"] is True
    assert len(rec.entries) == 1
    e = rec.entries[0]
    assert e["book"] == "candidate" and e["size"] == 1 and e["dte_bucket"] == "dipbuy"


def test_maybe_open_noop_when_not_triggered(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda d: False)
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out is None and rec.entries == []


def test_maybe_open_noop_when_disabled(monkeypatch):
    import config
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(config, "DIPBUY_FORWARD_ENABLED", False)
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda d: True)
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out is None and rec.entries == []
