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
    # Nav links to all eight views
    for href in ('href="/today"', 'href="/chat"', 'href="/"',
                 'href="/trades"', 'href="/journal"', 'href="/chats"',
                 'href="/macro"', 'href="/backtest"'):
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
            assert "Macro Snapshot"            in r.text
            assert "No volatility snapshot"    in r.text
            assert "No sector snapshot"        in r.text
        finally:
            mr._MACRO_DIR = old


def test_macro_page_renders_earnings_panel(client, app_modules, tmp_path):
    """Seed the earnings cache and verify /macro shows the panel."""
    import json
    from datetime import date, timedelta
    import config
    cache_path = os.path.join(str(tmp_path), "earnings_calendar.json")
    today = date.today()
    with open(cache_path, "w") as f:
        json.dump({
            "fetched_at": today.isoformat(),
            "entries": [
                {"ticker": "AAPL",
                 "earnings_date": (today + timedelta(days=3)).isoformat()},
                {"ticker": "MSFT",
                 "earnings_date": (today + timedelta(days=8)).isoformat()},
            ],
        }, f)

    r = client.get("/macro")
    assert r.status_code == 200
    assert "Watchlist Earnings" in r.text
    assert "AAPL"               in r.text
    assert "MSFT"               in r.text


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
        # Plain-English flag + signal labels now
        assert "Low (market expects calm)"                in r.text
        assert "Some sectors leading, others lagging"     in r.text
        assert "XLK"     in r.text
        assert "XLE"     in r.text
        # Raw enum names should NOT leak
        assert "CALM"     not in r.text
        assert "ROTATING" not in r.text
    finally:
        mr._MACRO_DIR = old


def test_today_page_empty(client, app_modules):
    """No plan for today — placeholder shown."""
    r = client.get("/today")
    assert r.status_code == 200
    # h1 gets html-escaped (apostrophe -> &#x27;) so look for the unambiguous bits
    assert "Today" in r.text and "Play" in r.text
    assert "No morning brief yet"  in r.text


def test_today_page_renders_full_brief(client, app_modules):
    """Seed a plan and verify all sections render."""
    from journal.plan_logger import PlanLogger
    from datetime import date

    plan = {
        "date":             date.today().isoformat(),
        "ticker":           "SPY",
        "regime":           "choppy_low_vol",
        "play":             "Iron Condor",
        "strategy":         "iron_condor",
        "rr_ratio":         "0.33",
        "recommended_dte":  14,
        "max_profit":       250,
        "max_loss":         750,
        "exit_rule":        "Close at 50% profit",
        "narrative":        "Iron condor still in play. VIX calm, sectors moderately rotating.",
        "skip_conditions":  ["Skip if VIX opens > 17", "Skip if SPY gaps > 0.6%"],
        "watch_conditions": ["Tighten condor if dispersion drops below 1.5"],
        "macro_context": {
            "vix_ts": {"flag": "calm", "ratio": 0.94},
            "sector": {"signal": "rotating", "dispersion": 2.1},
            "events": [{"event": "CPI", "days_away": 1}],
        },
    }
    PlanLogger().save_plan(plan)

    r = client.get("/today")
    assert r.status_code == 200
    # Regime renders as plain English, not raw "choppy_low_vol"
    assert "Sideways market, low volatility" in r.text
    assert "choppy_low_vol"                  not in r.text
    assert "Iron Condor"                      in r.text
    assert "$250"                             in r.text   # max profit
    assert "Skip if VIX opens"                in r.text   # original test seed
    assert "Tighten condor"                   in r.text
    assert "Iron condor still in play"        in r.text   # narrative fallback
    # VIX flag renders plain (not raw "calm")
    assert "Low (market expects calm)"        in r.text
    assert "CPI"                              in r.text


def test_today_page_uses_plain_summary_when_present(client, app_modules):
    """When the brief provides plain_summary, /today uses it instead of narrative."""
    from journal.plan_logger import PlanLogger
    from datetime import date
    PlanLogger().save_plan({
        "date":             date.today().isoformat(),
        "ticker":           "SPY",
        "regime":           "trending_up_calm",
        "play":             "Bull Call Debit Spread",
        "strategy":         "debit_spread",
        "plain_summary":    "Strong uptrend lets us buy cheap calls.",
        "narrative":        "ADX 38, +9% above 200MA, IVR=30",  # jargon — should NOT show
    })
    r = client.get("/today")
    assert "Strong uptrend lets us buy cheap calls" in r.text
    assert "ADX 38"                                  not in r.text   # narrative not used
    # Regime rendered plain
    assert "Steady uptrend, low volatility"          in r.text
    assert "trending_up_calm"                        not in r.text


def test_today_page_no_raw_regime_names(client, app_modules):
    """Guard: no raw enum string leaks into /today rendering."""
    from journal.plan_logger import PlanLogger
    from datetime import date
    for regime in ("trending_up_calm", "trending_down_calm",
                    "choppy_low_vol", "choppy_high_vol"):
        PlanLogger().save_plan({
            "date":   date.today().isoformat(),
            "ticker": "SPY",
            "regime": regime,
            "play":   "test",
            "strategy": "iron_condor",
        })
        r = client.get("/today")
        assert regime not in r.text, f"raw regime {regime!r} leaked into /today"


# ─────────────────────────────────────────
# Macro chat route
# ─────────────────────────────────────────

def test_macro_chat_page_empty_history_shows_examples(client, app_modules):
    r = client.get("/chat")
    assert r.status_code == 200
    assert "Macro Chat"                  in r.text
    assert "Context Claude sees"         in r.text
    # Examples shown when no history yet
    assert "Should I take today"         in r.text or "Ask anything" in r.text


def test_macro_chat_send_returns_reply_and_persists(client, app_modules, monkeypatch):
    """POST /chat with a stubbed MacroChat.ask returns the reply."""
    from alerts import macro_chat
    monkeypatch.setattr(
        macro_chat.MacroChat, "ask",
        lambda self, msg: f"[stub reply for: {msg}]",
    )
    r = client.post("/chat", json={"message": "what's the play today?"})
    assert r.status_code == 200
    assert r.json()["reply"] == "[stub reply for: what's the play today?]"


def test_macro_chat_send_rejects_empty(client, app_modules):
    r = client.post("/chat", json={"message": "   "})
    assert r.status_code == 400


def test_macro_chat_reset_clears_history(client, app_modules):
    from alerts.macro_chat import MacroChat
    mc = MacroChat()
    mc.append_turn("user", "hi")
    assert len(mc.history()) == 1
    r = client.post("/chat/reset")
    assert r.status_code == 200
    assert MacroChat().history() == []


# ─────────────────────────────────────────
# Backtest dashboard
# ─────────────────────────────────────────

def test_backtest_page_baseline_only(client, app_modules):
    """No hypotheses, no KB, no predictions — still renders production baseline."""
    r = client.get("/backtest")
    assert r.status_code == 200
    assert "Production Baseline"   in r.text
    assert "1.73"                  in r.text   # default Sharpe from docs
    assert "74.1%"                 in r.text   # iron condor edge
    assert "By Regime"             in r.text
    assert "Prediction Accuracy"   in r.text
    # Empty-state messages for the two dynamic sections
    assert "No hypotheses yet"     in r.text
    assert "No KB entries"         in r.text


def test_backtest_page_renders_hypotheses_and_kb(client, app_modules, tmp_path):
    """Seed a hypothesis spec + KB entry + a resolved prediction."""
    import json
    from datetime import date
    from learning.knowledge_base import KnowledgeBase, KBEntry
    from learning.predictions    import PredictionLog, Prediction

    # Seed an accepted hypothesis
    hyp_dir = os.path.join(str(tmp_path), "learning", "hypotheses")
    os.makedirs(hyp_dir, exist_ok=True)
    with open(os.path.join(hyp_dir, "hyp_a.json"), "w") as f:
        json.dump({
            "id": "hyp_a", "verdict": "accepted",
            "var": "ADX_TREND_MIN", "proposed_value": 27.0,
            "sharpe_delta": 0.15, "pnl_delta": 350.0,
            "rationale": "Tighter ADX gate cuts whipsaw losses",
        }, f)

    # Seed a KB entry + resolved prediction
    KnowledgeBase().append(KBEntry(
        date=date.today().isoformat(), category="market_context",
        claim="VIX flipped cautious after CPI beat", confidence=0.7,
    ))
    pl = PredictionLog()
    pl.save(Prediction(date="2026-05-15", regime="choppy_low_vol",
                        direction="bullish", tradeable=True, entry_spy=700.0))
    pl.mark_resolved("2026-05-15", actual_close=705.0,
                      outcome="correct", resolution_date="2026-05-15")

    r = client.get("/backtest")
    assert r.status_code == 200
    # Hypothesis details surfaced
    assert "ADX_TREND_MIN"                  in r.text
    assert "Tighter ADX gate"               in r.text
    assert "+0.15"                          in r.text or "0.15" in r.text
    # KB grouping shown
    assert "market_context"                 in r.text
    assert "VIX flipped cautious"           in r.text
    # Prediction accuracy box reflects the resolved entry
    assert "100.0%" in r.text or "100%" in r.text


# Needed by test_macro_page_empty
import os
