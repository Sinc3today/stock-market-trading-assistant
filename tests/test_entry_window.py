"""tests/test_entry_window.py -- the 09:45-15:00 ET entry-window guard.

No opens in the first 15 min after the bell or the last hour before close.
Exits are NOT gated by this (tested elsewhere); this only covers the helper.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# 2026-06-08 is a Monday (trading day); 2026-06-13 is a Saturday.
def _mon(h, m):
    return datetime(2026, 6, 8, h, m)


def test_open_allowed_midday():
    assert config.within_entry_window(_mon(10, 0)) is True
    assert config.within_entry_window(_mon(12, 30)) is True
    assert config.within_entry_window(_mon(14, 59)) is True


def test_blocked_before_0945():
    assert config.within_entry_window(_mon(9, 16)) is False    # the paper-broker incident
    assert config.within_entry_window(_mon(9, 30)) is False    # the bell itself
    assert config.within_entry_window(_mon(9, 44)) is False


def test_start_boundary_inclusive():
    assert config.within_entry_window(_mon(9, 45)) is True


def test_blocked_last_hour():
    assert config.within_entry_window(_mon(15, 0)) is False     # 15:00 exclusive
    assert config.within_entry_window(_mon(15, 30)) is False
    assert config.within_entry_window(_mon(16, 0)) is False


def test_blocked_on_weekend():
    assert config.within_entry_window(datetime(2026, 6, 13, 10, 0)) is False  # Saturday


def test_blocked_on_holiday():
    assert config.within_entry_window(datetime(2026, 6, 19, 10, 0)) is False  # Juneteenth


def test_enforce_flag_is_kill_switch(monkeypatch):
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)
    # with enforcement off, opens are allowed any time (even pre-market)
    assert config.within_entry_window(_mon(9, 16)) is True


# ── guard wiring: opens are actually blocked outside the window ──────────

def test_dipbuy_open_blocked_outside_window(monkeypatch):
    """maybe_open_dipbuy must NOT open when the window guard says no."""
    import pandas as pd
    from learning import dipbuy_forward
    monkeypatch.setattr(config, "within_entry_window", lambda *a, **k: False)
    # a clearly-oversold frame that WOULD trigger if the window were open
    df = pd.DataFrame({"close": [float(c) for c in range(460, 400, -1)]},
                      index=pd.bdate_range("2026-01-02", periods=60))
    out = dipbuy_forward.maybe_open_dipbuy(
        df, spot=400.0, ivr=50.0, options_layer=None, recorder=None)
    assert out is None      # blocked before touching options_layer/recorder


def test_paper_broker_logs_prediction_but_skips_open_outside_window(monkeypatch, tmp_path):
    """Decoupling (Standing Rule 15): outside the entry window the daily
    PREDICTION is still logged; only the OPEN is skipped."""
    from datetime import date
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "within_entry_window", lambda *a, **k: False)
    from learning.paper_broker import PaperBroker
    from learning.predictions import PredictionLog
    today = date.today().isoformat()
    play = {
        "date": today, "tradeable": True, "regime": "trending_up_calm",
        "confidence": 0.85, "reasons": ["ADX trend", "VIX calm"],
        "metrics": {"spy_close": 720.0, "vix": 14.0, "ivr": 32.0, "adx": 28.0},
        "options": {"strategy": "debit_spread",
                    "legs": [{"strike": 720, "side": "buy"}, {"strike": 730, "side": "sell"}],
                    "max_profit": "$700", "max_loss": "$300"},
    }
    res = PaperBroker().execute(play)
    # open skipped...
    assert res.get("skipped") == "entry_window"
    assert res.get("trade_id") is None
    # ...but the forecast was still recorded
    assert PredictionLog().get(today) is not None
