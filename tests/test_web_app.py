"""
tests/test_web_app.py -- TestClient coverage for alerts/web_app.py

Uses an isolated SQLite DB (per-test tmp file) and patches _ask_claude
so no Anthropic key is required.
"""

from __future__ import annotations

import importlib
import sys

import pytest


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture()
def app_modules(monkeypatch, tmp_path):
    """
    Reload alert_store + web_app against a fresh tmp DB so every test
    starts with empty tables. Also redirect LOG_DIR so TradeRecorder
    writes its trades.json to a per-test tmp file.
    """
    db_path = tmp_path / "alert_store.db"
    monkeypatch.setenv("ALERT_STORE_DB", str(db_path))

    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")

    # Drop cached imports so the new ALERT_STORE_DB env var takes effect.
    for mod in ("alerts.web_app", "alerts.alert_store"):
        sys.modules.pop(mod, None)

    alert_store = importlib.import_module("alerts.alert_store")
    web_app     = importlib.import_module("alerts.web_app")

    # Stub out the Anthropic call so chat doesn't need an API key.
    monkeypatch.setattr(
        web_app, "_ask_claude",
        lambda alert, msg, history: f"[stub reply to: {msg}]",
    )

    return alert_store, web_app


@pytest.fixture()
def client(app_modules):
    """FastAPI TestClient bound to the freshly reloaded app."""
    from fastapi.testclient import TestClient
    _, web_app = app_modules
    return TestClient(web_app.app)


@pytest.fixture()
def sample_alert():
    return {
        "ticker":     "SPY",
        "regime":     "CHOPPY_LOW_VOL",
        "play":       "iron_condor",
        "direction":  "neutral",
        "vix":        14.2,
        "ivr":        31,
        "adx":        18,
        "confidence": 85,
        "entry":      560,
        "stop":       550,
        "target":     575,
        "rr_ratio":   "1.5",
        "strategy":   "iron_condor",
        "source":     "spy_daily",
    }


# ─────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────

def test_health_check(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_alert_returns_404(client):
    r = client.get("/alerts/FAKEID")
    assert r.status_code == 404


def test_save_and_retrieve_alert(client, app_modules, sample_alert):
    alert_store, _ = app_modules
    alert_id = alert_store.save_alert(sample_alert)
    assert alert_id, "save_alert should return an id"

    r = client.get(f"/alerts/{alert_id}")
    assert r.status_code == 200
    body = r.text
    assert "SPY"            in body
    assert "CHOPPY_LOW_VOL" in body
    assert alert_id         in body


def test_journal_save_and_get(client, app_modules, sample_alert):
    alert_store, _ = app_modules
    alert_id = alert_store.save_alert(sample_alert)

    payload = {
        "took_trade":      True,
        "direction_agree": True,
        "notes":           "took the IC, sized 1 contract",
        "outcome":         "win",
        "pnl":             125.50,
    }
    save_resp = client.post(f"/alerts/{alert_id}/journal", json=payload)
    assert save_resp.status_code == 200
    assert save_resp.json() == {"ok": True}

    list_resp = client.get(f"/alerts/{alert_id}/journal")
    assert list_resp.status_code == 200
    entries = list_resp.json()
    assert len(entries) == 1
    assert entries[0]["notes"]      == "took the IC, sized 1 contract"
    assert entries[0]["outcome"]    == "win"
    assert entries[0]["took_trade"] == 1
    assert entries[0]["pnl"]        == 125.50


def test_chat_history_empty(client, app_modules, sample_alert):
    alert_store, _ = app_modules
    alert_id = alert_store.save_alert(sample_alert)

    history = alert_store.get_chat_history(alert_id)
    assert history == []

    # Page should still render without chat history.
    r = client.get(f"/alerts/{alert_id}")
    assert r.status_code == 200


def test_recent_alerts_list(client, app_modules, sample_alert):
    alert_store, _ = app_modules
    alert_store.save_alert(sample_alert)

    r = client.get("/")
    assert r.status_code == 200
    assert "Recent Alerts" in r.text
    assert "SPY"           in r.text


# ─────────────────────────────────────────
# Cross-alert aggregated views
# ─────────────────────────────────────────

def test_nav_appears_on_index(client):
    r = client.get("/")
    assert r.status_code == 200
    # Nav links to all five aggregated views
    for href in ('href="/"', 'href="/trades"', 'href="/journal"',
                 'href="/chats"', 'href="/macro"'):
        assert href in r.text


def test_trades_page_empty(client):
    r = client.get("/trades")
    assert r.status_code == 200
    assert "Trade History"        in r.text
    assert "No trades recorded"   in r.text


def test_trades_page_renders_trade(client, app_modules):
    from journal.trade_recorder import TradeRecorder
    tr = TradeRecorder()
    tid = tr.log_entry(ticker="AAPL", entry_price=170.0, size=10)
    tr.log_exit(tid, exit_price=182.0)

    r = client.get("/trades")
    assert r.status_code == 200
    assert "AAPL"      in r.text
    assert "WIN"       in r.text
    # P&L formatted with sign
    assert "+$" in r.text or "+120" in r.text


def test_journal_page_empty(client):
    r = client.get("/journal")
    assert r.status_code == 200
    assert "No journal entries yet" in r.text


def test_journal_page_renders_entry(client, app_modules, sample_alert):
    alert_store, _ = app_modules
    alert_id = alert_store.save_alert(sample_alert)
    alert_store.save_journal_entry(alert_id, {
        "took_trade": True, "direction_agree": True,
        "notes": "scaled in on the open",
        "outcome": "win", "pnl": 250.0,
    })

    r = client.get("/journal")
    assert r.status_code == 200
    assert "SPY"                   in r.text
    assert "scaled in on the open" in r.text
    assert "WIN"                   in r.text
    # The card links back to the alert detail
    assert f'/alerts/{alert_id}' in r.text


def test_chats_page_empty(client):
    r = client.get("/chats")
    assert r.status_code == 200
    assert "No chats yet" in r.text


def test_chats_page_renders_thread(client, app_modules, sample_alert):
    alert_store, _ = app_modules
    alert_id = alert_store.save_alert(sample_alert)
    alert_store.save_chat_message(alert_id, "user",      "what's the R/R look like?")
    alert_store.save_chat_message(alert_id, "assistant", "1.5 — workable for an iron condor.")

    r = client.get("/chats")
    assert r.status_code == 200
    assert "SPY"                                       in r.text
    assert "2 messages"                                in r.text
    # Last message preview (assistant's)
    assert "workable for an iron condor"               in r.text
    assert f'/alerts/{alert_id}'                       in r.text


def test_macro_page_empty(client, app_modules):
    """No snapshot files yet — page renders 'no snapshot' for both panels."""
    _, web_app = app_modules
    # Force the macro_runner to look at an empty tmp dir.
    import signals.macro_runner as mr
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        old = mr._MACRO_DIR
        mr._MACRO_DIR = d
        try:
            r = client.get("/macro")
            assert r.status_code == 200
            assert "Macro Snapshot"     in r.text   # h1 always present
            assert "No VIX snapshot"    in r.text
            assert "No sector snapshot" in r.text
        finally:
            mr._MACRO_DIR = old


def test_macro_page_renders_snapshots(client, app_modules, tmp_path):
    """Seed both snapshot files and verify the page surfaces values + flags."""
    import json
    import signals.macro_runner as mr

    old = mr._MACRO_DIR
    mr._MACRO_DIR = str(tmp_path)
    try:
        with open(os.path.join(str(tmp_path), "vix_latest.json"), "w") as f:
            json.dump({"VIX9D": 14.0, "VIX": 15.0, "VIX3M": 16.0, "VIX6M": 17.0,
                       "ratio": 0.94, "flag": "calm", "asof": "2026-05-16T20:00:00"}, f)
        with open(os.path.join(str(tmp_path), "sector_latest.json"), "w") as f:
            json.dump({
                "leaders":  [("XLK", 4.2), ("XLY", 3.0), ("XLF", 2.5)],
                "laggards": [("XLE", -3.5), ("XLU", -2.1), ("XLP", -1.4)],
                "dispersion": 2.10, "signal": "rotating",
                "rs": {"XLK": 4.2}, "asof": "2026-05-16T20:30:00", "horizon": 20,
            }, f)

        r = client.get("/macro")
        assert r.status_code == 200
        assert "CALM"     in r.text
        assert "ROTATING" in r.text
        assert "XLK"      in r.text
        assert "XLE"      in r.text
        assert "0.940"    in r.text or "0.94" in r.text
    finally:
        mr._MACRO_DIR = old


# Needed by test_macro_page_empty
import os
