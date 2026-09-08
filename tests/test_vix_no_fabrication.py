"""tests/test_vix_no_fabrication.py -- a missing VIX is not a VIX.

get_current() returned a hardcoded 20.0 when every source failed, typed float,
never None. That did three things at once:

  1. The fabricated sigma priced every open spread, and the resulting number
     was written as a real exit price via log_exit.
  2. It silently killed a fallback chain built for exactly this case —
     exit_manager._fetch_vix wraps it in try/except, so its yfinance fallback
     and documented 18.0 default were dead code, and manage_open's
     `if vix is None: skipping` guard could never fire.
  3. 20.0 straddles VIX_CALM_MAX=18.0, so an outage deterministically flipped
     the regime OUT of "calm" and that flip was persisted as a measurement.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import data.vix_client as vix_mod
from data.vix_client import VIXClient


@pytest.fixture(autouse=True)
def _clear_vix_cache():
    """The VIX cache is a MODULE-level global, so it leaks between tests and
    makes them order-dependent. (No conftest.py exists yet — see the audit.)"""
    vix_mod._cache.update({"vix": None, "fetched_at": None,
                           "df": None, "df_at": None})
    yield
    vix_mod._cache.update({"vix": None, "fetched_at": None,
                           "df": None, "df_at": None})


def _blind(client, monkeypatch):
    """Every source fails."""
    for name in ("_fetch_polygon_latest", "_fetch_yfinance_latest",
                 "_fetch_cboe_latest"):
        if hasattr(client, name):
            monkeypatch.setattr(client, name, lambda *a, **k: None)
    return client


def test_total_failure_returns_none_not_a_number(monkeypatch):
    c = _blind(VIXClient(), monkeypatch)
    assert c.get_current() is None


def test_it_never_returns_the_old_hardcoded_fallback(monkeypatch):
    c = _blind(VIXClient(), monkeypatch)
    assert c.get_current() != 20.0


def test_a_working_source_is_unaffected(monkeypatch):
    c = VIXClient()
    c._cache = None
    monkeypatch.setattr(c, "_fetch_polygon_latest", lambda *a, **k: 14.5)
    assert c.get_current() == pytest.approx(14.5)


def test_exit_manager_skips_marking_when_vix_is_unknown(tmp_path, monkeypatch):
    """The guard that could never fire must now actually fire — marking a
    spread with a fabricated sigma writes a fabricated exit price."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)
    from journal.trade_recorder import TradeRecorder
    from learning.exit_manager import ExitManager

    rec = TradeRecorder()
    rec.log_entry("SPY", 1.60, 1, strategy="iron_condor", book="disciplined",
                  notes="[AUTO-PAPER] test", dte_bucket="45DTE",
                  legs=[{"action": "SELL", "option_type": "call", "strike": 800,
                         "expiry": "2026-12-18"}])

    em = ExitManager.__new__(ExitManager)
    em.vix, em.polygon, em.trades = None, None, rec
    monkeypatch.setattr(ExitManager, "_fetch_vix", lambda self: None)
    monkeypatch.setattr(ExitManager, "_fetch_spy_close", lambda self: 770.0)

    assert em.manage_open() == []
    assert rec.get_trade_by_id(rec.get_all_trades()[0]["trade_id"])["outcome"] == "open"


def test_regime_is_not_flipped_by_an_outage(monkeypatch):
    """20.0 sat above VIX_CALM_MAX=18, so an outage deterministically read as
    'not calm' and was persisted as if measured."""
    from signals.regime_detector import VIX_CALM_MAX
    c = _blind(VIXClient(), monkeypatch)
    v = c.get_current()
    assert v is None, "an unknown VIX must not be comparable to a threshold"
    # The old fallback sat ABOVE the calm threshold, so an outage read as
    # "not calm" every single time and was persisted as a measurement.
    assert 20.0 > VIX_CALM_MAX
