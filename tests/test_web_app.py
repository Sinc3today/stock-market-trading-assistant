"""
tests/test_web_app.py -- TestClient coverage for alerts/web_app.py

Uses an isolated SQLite DB (per-test tmp file) and patches _ask_claude
so no Anthropic key is required.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile

import pytest


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture()
def app_modules(monkeypatch, tmp_path):
    """
    Reload alert_store + web_app against a fresh tmp DB so every test
    starts with empty tables.
    """
    db_path = tmp_path / "alert_store.db"
    monkeypatch.setenv("ALERT_STORE_DB", str(db_path))

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
