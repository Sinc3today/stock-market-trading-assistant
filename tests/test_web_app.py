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

    # 2026-07-10 cleanup: the feed moved to /alerts ("/" is now the copilot)
    r = client.get("/alerts")
    assert r.status_code == 200
    assert "Recent Alerts" in r.text
    assert "SPY"           in r.text


# ─────────────────────────────────────────
# Cross-alert aggregated views
# ─────────────────────────────────────────

def test_nav_appears_on_index(client):
    # "/" now lands on the copilot with the lean 4-item nav
    r = client.get("/")
    assert r.status_code == 200
    for href in ('href="/copilot"', 'href="/today"',
                 'href="/trades"', 'href="/learning"'):
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


def test_macro_page_renders_baseline_card_from_static_defaults(client, app_modules):
    """No backtest_summary.json on disk → card renders from static defaults
    and points the user at the rerun CLI."""
    r = client.get("/macro")
    assert r.status_code == 200
    assert "Tuned baseline" in r.text
    # Static defaults: 65.2% / 3.58 / 5y (cap + ADX32 + VIX18, walk-forward validated)
    assert "65.2%"          in r.text
    assert "3.58"           in r.text
    assert "backtests.rerun" in r.text   # call-to-action mentions the CLI


def test_macro_page_baseline_card_uses_fresh_summary(client, app_modules, tmp_path):
    """When logs/backtest_summary.json exists, the card uses its numbers
    and labels the source as a fresh rerun."""
    import json
    summary = {
        "source":  "rerun_cli (local)",
        "version": "2026-05-17",
        "years":   3,
        "overview": {"sharpe": 1.91, "win_rate_pct": 52.4,
                     "trade_days": 410, "skip_days": 320},
        "by_regime":  [],
        "thresholds": {},
    }
    with open(os.path.join(str(tmp_path), "backtest_summary.json"), "w") as f:
        json.dump(summary, f)

    r = client.get("/macro")
    assert r.status_code == 200
    assert "Tuned baseline" in r.text
    assert "52.4%"          in r.text
    assert "1.91"           in r.text
    assert "Fresh rerun"    in r.text


def test_levels_page_renders_when_polygon_fails(client, app_modules, monkeypatch):
    """If SPY fetch fails, the page should still render gracefully with the
    'no data' empty state, not 500."""
    import data.polygon_client as pc
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("polygon down")))
    r = client.get("/levels")
    assert r.status_code == 200
    assert "SPY Levels" in r.text
    # Plotly CDN still loaded
    assert "cdn.plot.ly" in r.text


def test_levels_page_renders_chart_with_seeded_spy(client, app_modules, monkeypatch):
    """With injected SPY bars, chart container + MAs + levels card render."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    rows = []
    base = datetime(2026, 1, 5)
    for i in range(250):
        d = base + timedelta(days=i)
        rows.append({"timestamp": d, "open": 500+i*0.1, "high": 500+i*0.1+1,
                     "low": 500+i*0.1-1, "close": 500+i*0.1, "volume": 1_000_000})
    df = pd.DataFrame(rows).set_index("timestamp")

    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)

    # Stub OptionsChain so /levels doesn't try the real Polygon options call
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels")
    assert r.status_code == 200
    assert "SPY Levels"   in r.text
    assert "lvl-chart"    in r.text
    assert "Plotly.newPlot" in r.text
    assert "MA20"         in r.text
    assert "Price levels" in r.text


def test_levels_page_renders_option_walls_when_chain_present(client, app_modules, monkeypatch):
    """When load_walls returns data, the walls card lists call + put + max pain."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    rows = []
    for i in range(50):
        d = datetime(2026, 3, 1) + timedelta(days=i)
        rows.append({"timestamp": d, "open": 500, "high": 501, "low": 499,
                     "close": 500, "volume": 1_000_000})
    df = pd.DataFrame(rows).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)

    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls", lambda *a, **kw: {
        "call_walls": [{"strike": 515.0, "open_interest": 20_000, "distance_pct": 3.0,
                        "side": "call"}],
        "put_walls":  [{"strike": 490.0, "open_interest": 15_000, "distance_pct": -2.0,
                        "side": "put"}],
        "max_pain":   505.0,
        "spot":       500.0,
        "expiration": "2026-06-20",
    })

    r = client.get("/levels")
    assert r.status_code == 200
    assert "Heavy option strikes" in r.text
    assert "Call wall"            in r.text
    assert "$515"                 in r.text
    assert "Put wall"             in r.text
    assert "$490"                 in r.text
    assert "Max pain"             in r.text
    assert "$505"                 in r.text
    assert "expiry 2026-06-20"    in r.text


def test_levels_per_ticker_route_renders_with_ticker_heading(
    client, app_modules, monkeypatch
):
    """GET /levels/AAPL renders with AAPL in the heading + page title."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    rows = []
    for i in range(60):
        d = datetime(2026, 3, 1) + timedelta(days=i)
        rows.append({"timestamp": d, "open": 200, "high": 201, "low": 199,
                     "close": 200, "volume": 1_000_000})
    df = pd.DataFrame(rows).set_index("timestamp")

    captured = {"ticker": None}
    def fake_get_bars(self, ticker, **kw):
        captured["ticker"] = ticker
        return df
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", fake_get_bars)

    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels/AAPL")
    assert r.status_code == 200
    assert "AAPL Levels"   in r.text
    assert "AAPL"          in r.text
    # The fetch actually used AAPL, not the default SPY
    assert captured["ticker"] == "AAPL"


def test_levels_per_ticker_invalid_falls_back_to_spy(
    client, app_modules, monkeypatch
):
    """Garbage in the path → falls back to SPY rather than passing junk
    to Polygon (which could be a server log/abuse vector)."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    rows = []
    for i in range(30):
        rows.append({"timestamp": datetime(2026, 3, 1) + timedelta(days=i),
                     "open": 500, "high": 501, "low": 499, "close": 500,
                     "volume": 1_000_000})
    df = pd.DataFrame(rows).set_index("timestamp")

    captured = {"ticker": None}
    def fake_get_bars(self, ticker, **kw):
        captured["ticker"] = ticker
        return df
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", fake_get_bars)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels/.._not_a_ticker_!")
    assert r.status_code == 200
    assert "SPY Levels" in r.text   # fallback heading
    assert captured["ticker"] == "SPY"


def test_levels_picker_form_populated_with_watchlist(
    client, app_modules, monkeypatch, tmp_path
):
    """The picker <select> should include SPY plus the union of watchlist
    sections (swing/intraday/options_enabled)."""
    import json
    wl_path = tmp_path / "watchlist.json"
    wl_path.write_text(json.dumps({
        "swing":           ["AAPL", "MSFT"],
        "intraday":        ["NVDA"],
        "options_enabled": ["TSLA"],
    }))
    import config
    monkeypatch.setattr(config, "WATCHLIST_PATH", str(wl_path))

    # Stub Polygon + walls so the page renders fast.
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc
    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500, "high": 501, "low": 499, "close": 500, "volume": 1
    } for i in range(30)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels")
    assert r.status_code == 200
    for sym in ("SPY", "AAPL", "MSFT", "NVDA", "TSLA"):
        assert f'<option value="{sym}"' in r.text
    # SPY is selected by default
    assert '<option value="SPY" selected' in r.text


def test_mobile_css_media_query_present(client, app_modules):
    """Every page should ship the responsive @media rules (sidebar collapses)."""
    r = client.get("/today")
    assert "@media (max-width:860px)" in r.text
    # On desktop the layout is a sidebar + content grid
    assert "grid-template-columns:var(--sidebar-w)" in r.text
    # ...which collapses to a single column on mobile
    assert "grid-template-columns:1fr" in r.text


def test_nav_includes_levels_link(client, app_modules):
    # 2026-07-10 cleanup: levels is retired from the nav (route stays alive)
    r = client.get("/macro")
    assert 'href="/levels"' not in r.text
    assert client.get("/levels").status_code == 200


# ─────────────────────────────────────────
# Hamburger nav + group structure
# ─────────────────────────────────────────

def test_nav_renders_brand_and_hamburger_toggle(client, app_modules):
    r = client.get("/macro")
    assert "nav-brand"          in r.text
    assert "nav-toggle"         in r.text
    assert "nav-toggle-input"   in r.text
    # Sidebar + theme toggle + the CSS-only drawer (:checked sibling selector)
    assert "theme-toggle"          in r.text
    assert ":checked ~ .nav"       in r.text


def test_nav_renders_lean_flat_list(client, app_modules):
    # 2026-07-10 cleanup: one flat group, four core pages, SVG icons (no emoji)
    r = client.get("/macro")
    assert ">Now<" not in r.text and ">Tools<" not in r.text
    assert r.text.count('class="nav-ic"') >= 4


def test_nav_groups_contain_expected_links(client, app_modules):
    r = client.get("/today")
    for href in ("/copilot", "/today", "/trades", "/learning"):
        assert f'href="{href}"' in r.text
    # retired pages are OUT of the nav
    for href in ("/journal", "/chats", "/backtest", "/macro"):
        assert f'href="{href}"' not in r.text


# ─────────────────────────────────────────
# /levels: cookie persistence + auto-refresh
# ─────────────────────────────────────────

def test_levels_default_uses_cookie_when_no_query(client, app_modules, monkeypatch):
    """A returning visit with the levels_ticker cookie should land on that
    ticker, not SPY."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    captured = {"ticker": None}
    def fake_get_bars(self, ticker, **kw):
        captured["ticker"] = ticker
        return df
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", fake_get_bars)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels", cookies={"levels_ticker": "NVDA"})
    assert r.status_code == 200
    assert "NVDA Levels"     in r.text
    assert captured["ticker"] == "NVDA"


def test_levels_route_sets_cookie_on_response(client, app_modules, monkeypatch):
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels/AAPL")
    assert r.cookies.get("levels_ticker") == "AAPL"


def test_levels_query_param_overrides_cookie(client, app_modules, monkeypatch):
    """?ticker= takes precedence over the cookie (so the picker form
    always lands where the user just clicked)."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc
    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    captured = {"ticker": None}
    def fake_get_bars(self, ticker, **kw):
        captured["ticker"] = ticker
        return df
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", fake_get_bars)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels?ticker=TSLA", cookies={"levels_ticker": "NVDA"})
    assert r.status_code == 200
    assert "TSLA Levels"      in r.text
    assert captured["ticker"] == "TSLA"


def test_levels_page_includes_auto_refresh_meta(client, app_modules, monkeypatch):
    import data.polygon_client as pc
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: None)
    r = client.get("/levels")
    assert 'http-equiv="refresh"' in r.text
    assert 'content="300"'        in r.text


def test_other_pages_do_not_auto_refresh(client, app_modules):
    """The refresh meta tag should only land on /levels; /today, /macro,
    /chat etc must stay stable (chat would lose typing if it refreshed)."""
    for path in ("/today", "/macro", "/chat", "/"):
        r = client.get(path)
        assert 'http-equiv="refresh"' not in r.text


# ─────────────────────────────────────────
# /today SPY sparkline thumbnail
# ─────────────────────────────────────────

def test_today_renders_sparkline_when_spy_data_available(client, app_modules, monkeypatch):
    """/today fetches SPY closes and embeds a sparkline + link to /levels/SPY."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500 + i, "high": 501 + i, "low": 499 + i,
        "close": 500 + i, "volume": 1,
    } for i in range(35)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)

    # Seed a plan so /today doesn't short-circuit to the empty card
    from journal.plan_logger import PlanLogger
    from datetime import date
    PlanLogger().save_plan({
        "date":      date.today().isoformat(),
        "regime":    "trending_up_calm",
        "action":    "BUY",
        "strategy":  "debit_spread",
        "play":      "Bull debit",
        "rr_ratio":  "2.0",
        "recommended_dte": 30,
        "max_profit": 150,
        "max_loss":  200,
        "exit_rule": "...",
        "thesis":    "Steady uptrend",
    })

    r = client.get("/today")
    assert r.status_code == 200
    # Sparkline is an inline SVG with the SPY thumbnail card linking to /levels/SPY
    assert 'href="/levels/SPY"' in r.text
    assert "<svg"               in r.text
    assert "<polyline"          in r.text


def test_today_renders_without_sparkline_when_polygon_fails(client, app_modules, monkeypatch):
    import data.polygon_client as pc
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("polygon down")))
    from journal.plan_logger import PlanLogger
    from datetime import date
    PlanLogger().save_plan({
        "date":      date.today().isoformat(),
        "regime":    "trending_up_calm",
        "action":    "BUY",
        "strategy":  "debit_spread",
        "play":      "Bull debit",
        "rr_ratio":  "2.0",
        "recommended_dte": 30,
        "max_profit": 150,
        "max_loss":  200,
        "exit_rule": "...",
        "thesis":    "Steady uptrend",
    })
    r = client.get("/today")
    assert r.status_code == 200
    # No sparkline CHART when Polygon fails (nav icons legitimately contain
    # <polyline> now, so target the chart class instead of the raw tag)
    assert 'class="spark"'    not in r.text
    # H1 gets HTML-escaped, so check for the escaped form
    assert "Today&#x27;s Play"     in r.text


def test_sparkline_empty_list_returns_empty_string(client, app_modules):
    _, web_app = app_modules
    assert web_app._render_sparkline_svg([])     == ""
    assert web_app._render_sparkline_svg([1.0])  == ""


def test_sparkline_uses_green_when_up_red_when_down(client, app_modules):
    _, web_app = app_modules
    up   = web_app._render_sparkline_svg([100.0, 105.0])
    down = web_app._render_sparkline_svg([100.0, 95.0])
    assert "#3fb950" in up
    assert "#f85149" in down


# ─────────────────────────────────────────
# Hamburger auto-close on link tap
# ─────────────────────────────────────────

def test_nav_includes_auto_close_script(client, app_modules):
    """Tapping a link on mobile should close the hamburger panel before
    the next page loads. Verify the inline script is present and targets
    the right elements."""
    r = client.get("/macro")
    assert 'getElementById("nav-toggle")'          in r.text
    assert 'querySelectorAll(".nav a")'            in r.text
    assert 't.checked=false'                       in r.text


# ─────────────────────────────────────────
# /today wall summary card
# ─────────────────────────────────────────

def _seed_today_plan():
    from journal.plan_logger import PlanLogger
    from datetime import date
    PlanLogger().save_plan({
        "date":     date.today().isoformat(),
        "regime":   "trending_up_calm",
        "action":   "BUY",
        "strategy": "debit_spread",
        "play":     "Bull debit",
        "rr_ratio": "2.0",
        "recommended_dte": 30,
        "max_profit": 150, "max_loss": 200,
        "exit_rule": "...", "thesis":   "Steady uptrend",
    })


def _stub_spy_bars(monkeypatch, closes: list[float]):
    """Stub PolygonClient.get_bars to return a DataFrame whose last N closes
    match `closes`."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    rows = []
    for i, c in enumerate(closes):
        d = datetime(2026, 3, 1) + timedelta(days=i)
        rows.append({"timestamp": d, "open": c, "high": c+1, "low": c-1,
                     "close": c, "volume": 1})
    df = pd.DataFrame(rows).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)


def test_today_renders_wall_summary_when_walls_available(
    client, app_modules, monkeypatch,
):
    """When SPY closes + walls both available, /today shows the
    'where SPY sits vs heavy strikes' card with call/put/max-pain rows."""
    _seed_today_plan()
    _stub_spy_bars(monkeypatch, [500.0] * 28 + [505.0, 510.0])

    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls", lambda *a, **kw: {
        "call_walls": [{"strike": 520.0, "open_interest": 20_000,
                        "distance_pct": 2.0, "side": "call"}],
        "put_walls":  [{"strike": 495.0, "open_interest": 18_000,
                        "distance_pct": -3.0, "side": "put"}],
        "max_pain":   505.0,
        "spot":       510.0,
        "expiration": "2026-06-20",
    })

    r = client.get("/today")
    assert r.status_code == 200
    assert "Where SPY sits vs heavy strikes" in r.text
    assert "Resistance (call wall)"          in r.text
    assert "Support (put wall)"              in r.text
    assert "Max pain"                        in r.text
    assert "$520"                            in r.text
    assert "$495"                            in r.text
    assert "expiry 2026-06-20"               in r.text


def test_today_omits_wall_summary_when_walls_empty(
    client, app_modules, monkeypatch,
):
    """No walls → the summary card is silently dropped (page still 200s)."""
    _seed_today_plan()
    _stub_spy_bars(monkeypatch, [500.0, 501.0, 502.0])

    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls", lambda *a, **kw: {
        "call_walls": [], "put_walls": [], "max_pain": None,
        "spot": 502.0, "expiration": None,
    })

    r = client.get("/today")
    assert r.status_code == 200
    assert "Where SPY sits vs heavy strikes" not in r.text


def test_levels_renders_timeframe_ribbon_with_default_active(
    client, app_modules, monkeypatch,
):
    """The ribbon should show every timeframe button and mark the default
    range (3M) as active when no ?range= is passed."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500, "high": 501, "low": 499, "close": 500, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels")
    assert r.status_code == 200
    # Every range key has a button rendered
    for label in ("1D", "7D", "14D", "1M", "3M", "6M", "1Y", "5Y", "All"):
        assert f'>{label}<' in r.text
    # 3M (default) is active
    assert 'rng-btn active" href="/levels/SPY?range=3m">3M' in r.text


def test_levels_query_range_overrides_default(
    client, app_modules, monkeypatch,
):
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500, "high": 501, "low": 499, "close": 500, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    captured = {"timeframe": None, "days_back": None}
    def fake_get_bars(self, ticker, **kw):
        captured["timeframe"] = kw.get("timeframe")
        captured["days_back"] = kw.get("days_back")
        return df
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", fake_get_bars)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels/AAPL?range=1d")
    assert r.status_code == 200
    # 1D range uses 5-minute intraday bars, not daily
    assert captured["timeframe"] == "5min"
    # Active button switches to 1D
    assert 'rng-btn active" href="/levels/AAPL?range=1d">1D' in r.text


def test_levels_range_cookie_remembered_across_requests(
    client, app_modules, monkeypatch,
):
    """After you pick a range, the cookie persists it so reloads keep it."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc

    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500, "high": 501, "low": 499, "close": 500, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r1 = client.get("/levels/SPY?range=6m")
    assert r1.cookies.get("levels_range") == "6m"

    # Subsequent visit with no ?range= reads the cookie
    r2 = client.get("/levels", cookies={"levels_range": "6m", "levels_ticker": "SPY"})
    assert 'rng-btn active" href="/levels/SPY?range=6m">6M' in r2.text


def test_levels_invalid_range_falls_back_to_default(
    client, app_modules, monkeypatch,
):
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc
    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500, "high": 501, "low": 499, "close": 500, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels?range=42z")
    # Falls back to 3M default rather than crashing or hitting Polygon junk
    assert 'rng-btn active" href="/levels/SPY?range=3m">3M' in r.text


def test_resample_bars_collapses_daily_to_weekly(client, app_modules):
    """The 6M+ ranges resample daily bars to weekly so 200d isn't 200 candles."""
    import pandas as pd
    from datetime import datetime, timedelta
    _, web_app = app_modules
    rows = []
    for i in range(60):  # ~12 weeks of daily bars
        d = datetime(2026, 1, 5) + timedelta(days=i)
        rows.append({"timestamp": d, "open": 100+i, "high": 102+i,
                     "low": 99+i, "close": 101+i, "volume": 1_000_000})
    df = pd.DataFrame(rows).set_index("timestamp")
    weekly = web_app._resample_bars(df, "W-FRI")
    # 60 calendar days ≈ 8-10 weeks. The point is "many fewer than 60".
    assert 7 <= len(weekly) <= 12
    assert len(weekly) < len(df) / 4
    # Each bar still has OHLCV
    assert all(c in weekly.columns for c in ("open", "high", "low", "close", "volume"))


def test_gestures_script_present_on_every_page(client, app_modules):
    """Pull-to-refresh + swipe-back are injected by _render_page so every
    page should ship them — not just /levels."""
    for path in ("/today", "/macro", "/", "/trades", "/journal"):
        r = client.get(path)
        assert "ptr-indicator"   in r.text, f"missing PTR indicator on {path}"
        assert "Pull to refresh" in r.text, f"missing PTR label on {path}"
        # swipe-back guard: /today is the home, others can be swiped back from
        assert 'HOME_NAV_KEY  = "today"' in r.text


def test_active_nav_marker_on_body(client, app_modules):
    """Body data-active-nav attribute is what the swipe-back JS reads to
    decide whether to allow popping history. Verify the marker for each page."""
    cases = [("/today", "today"), ("/macro", "macro"), ("/", "copilot"),
             ("/trades", "trades"), ("/journal", "journal")]
    for path, expected in cases:
        r = client.get(path)
        assert f'data-active-nav="{expected}"' in r.text


def test_chart_modebar_is_enabled(client, app_modules, monkeypatch):
    """The user previously couldn't zoom back out — confirm the modebar
    config in the rendered script no longer disables it."""
    import pandas as pd
    from datetime import datetime, timedelta
    import data.polygon_client as pc
    df = pd.DataFrame([{
        "timestamp": datetime(2026, 3, 1) + timedelta(days=i),
        "open": 500, "high": 501, "low": 499, "close": 500, "volume": 1,
    } for i in range(40)]).set_index("timestamp")
    monkeypatch.setattr(pc.PolygonClient, "__init__", lambda self: None)
    monkeypatch.setattr(pc.PolygonClient, "get_bars", lambda *a, **kw: df)
    import signals.options_walls as ow
    monkeypatch.setattr(ow, "load_walls",
                        lambda *a, **kw: {"call_walls": [], "put_walls": [],
                                          "max_pain": None, "spot": kw.get("spot"),
                                          "expiration": None})

    r = client.get("/levels")
    assert "displayModeBar: false" not in r.text
    assert "modeBarButtonsToRemove" in r.text


def test_wall_summary_picks_nearest_above_below_spot(client, app_modules):
    """Helper picks the nearest call wall above spot + nearest put wall
    below — not the first ones in the list."""
    _, web_app = app_modules
    walls = {
        "call_walls": [
            {"strike": 530.0, "open_interest": 30_000, "distance_pct": 6.0, "side": "call"},
            {"strike": 515.0, "open_interest": 10_000, "distance_pct": 3.0, "side": "call"},
        ],
        "put_walls":  [
            {"strike": 480.0, "open_interest": 25_000, "distance_pct": -4.0, "side": "put"},
            {"strike": 495.0, "open_interest":  8_000, "distance_pct": -1.0, "side": "put"},
        ],
        "max_pain":   500.0,
        "expiration": "2026-06-20",
    }
    html_out = web_app._render_spy_walls_summary(walls, spot=500.0)
    # Nearest above spot = 515, nearest below = 495 (NOT 530 or 480)
    assert "$515" in html_out
    assert "$495" in html_out
    assert "$530" not in html_out
    assert "$480" not in html_out


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
    assert "3.58"                  in r.text   # default Sharpe (cap + ADX32 + VIX18)
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


def test_copilot_log_prefilled_from_calculator(client):
    # the condor calculator's "Log this condor" link opens /copilot/log with the
    # strikes as query params -> the form should come up pre-filled.
    r = client.get("/copilot/log?ticker=SPY&expiry=2026-08-14"
                   "&bc=794&sc=789&bp=701&sp=706")
    assert r.status_code == 200
    assert 'value="789"' in r.text and 'value="706"' in r.text
    assert "confirm your real credit" in r.text.lower()


def test_copilot_log_blank_without_params(client):
    r = client.get("/copilot/log")
    assert r.status_code == 200
    assert "Extract from screenshot" in r.text


# Needed by test_macro_page_empty
import os


# ─────────────────────────────────────────
# COPILOT HOME REDESIGN (2026-07-13): collapsible positions/calcs +
# today's-play card under the price with an expandable "why" panel
# ─────────────────────────────────────────

def _seed_live_condor_trade(tmp_log_dir):
    from journal.trade_recorder import TradeRecorder
    r = TradeRecorder()
    return r.log_entry(
        ticker="SPY", entry_price=1.45, size=3, trade_type="iron_condor",
        strategy="iron_condor", direction="neutral", mode="swing",
        legs=[
            {"action": "BUY",  "option_type": "CALL", "strike": 790, "expiry": "2026-08-21"},
            {"action": "SELL", "option_type": "CALL", "strike": 785, "expiry": "2026-08-21"},
            {"action": "BUY",  "option_type": "PUT",  "strike": 708, "expiry": "2026-08-21"},
            {"action": "SELL", "option_type": "PUT",  "strike": 713, "expiry": "2026-08-21"},
        ],
        max_profit=435.0, max_loss=1065.0, book="live",
    )


def _quiet_copilot_net(monkeypatch, web_app, plan=None, walls=None):
    """Kill every network fetch the copilot page makes."""
    monkeypatch.setattr(web_app, "_spy_spot", lambda: 750.0)
    monkeypatch.setattr(web_app, "_spy_vix", lambda: 15.0)
    monkeypatch.setattr(web_app, "_position_mtm_cached", lambda t: None)
    monkeypatch.setattr(web_app, "_fetch_spy_walls_for_today",
                        lambda spot: walls if walls is not None else {})

    class _PL:
        def get_plan(self, d): return plan
    monkeypatch.setattr(web_app, "PlanLogger", _PL)


def _sample_plan():
    return {
        "date": "2026-07-13", "ticker": "SPY", "regime": "trending_up_calm",
        "play": "BULL PUT CREDIT SPREAD — sell the put side",
        "confidence": 0.85, "strategy": "credit_spread",
        "legs": [
            {"action": "buy",  "option_type": "put", "strike": 747.0,
             "expiration": "2026-08-28"},
            {"action": "sell", "option_type": "put", "strike": 752.0,
             "expiration": "2026-08-28"},
        ],
        "max_profit": 171.0, "max_loss": 329.0,
        "regime_metrics": {"spy_close": 751.7, "ma200": 694.0,
                           "ma200_dist_%": 8.31, "adx": 34.6,
                           "vix": 15.8, "ivr": 50.0},
        "thesis": "ADX 34.6 >= 32.0 (trending) | SPY +8.3% above 200MA",
        "forecast": {"direction": "bullish", "confidence": 0.9,
                     "reasons": ["price vs MA20 up", "RSI 58 > 55"]},
        "plain_summary": "I'm selling a put spread below the market.",
        "skip_conditions": ["I'll skip if the market opens down big."],
        "exit_rule": "Close at 70% of max profit",
    }


def test_copilot_positions_are_collapsed_details(client, app_modules, monkeypatch):
    _, web_app = app_modules
    _quiet_copilot_net(monkeypatch, web_app)
    _seed_live_condor_trade(None)
    html = client.get("/copilot").text
    # each live position renders as a collapsed <details> fold
    assert '<details class="fold pos-fold"' in html
    assert '<details class="fold pos-fold" open' not in html
    # summary row: ticker + strategy + status; legs live INSIDE the fold
    assert "iron condor" in html
    assert "SELL $785 CALL" in html


def test_copilot_calc_cards_are_collapsed_details(client, app_modules, monkeypatch):
    _, web_app = app_modules
    _quiet_copilot_net(monkeypatch, web_app)
    html = client.get("/copilot").text
    # condor + butterfly calculators fold away (they were the longest cards)
    assert html.count('<details class="fold calc-fold"') == 2
    assert '<details class="fold calc-fold" open' not in html


def test_copilot_todays_play_sits_under_the_price_row(client, app_modules, monkeypatch):
    _, web_app = app_modules
    _quiet_copilot_net(monkeypatch, web_app, plan=_sample_plan())
    html = client.get("/copilot").text
    # play headline visible without any tap
    assert "BULL PUT CREDIT SPREAD" in html
    assert "SELL $752 PUT" in html            # RH-shaped legs visible
    # order: SPY stat card, then today's play, then live positions
    i_price = html.index("Market <span")
    i_play  = html.index("Today&#x27;s play") if "Today&#x27;s play" in html \
        else html.index("Today's play")
    i_pos   = html.index("Live positions")
    assert i_price < i_play < i_pos


def test_copilot_why_panel_expands_with_regime_signals_levels(client, app_modules, monkeypatch):
    _, web_app = app_modules
    walls = {"call_walls": [{"strike": 760.0, "open_interest": 9000}],
             "put_walls":  [{"strike": 740.0, "open_interest": 8000}],
             "max_pain": 750.0}
    _quiet_copilot_net(monkeypatch, web_app, plan=_sample_plan(), walls=walls)
    html = client.get("/copilot").text
    assert '<details class="fold why-fold"' in html
    # regime + signals + S/R data inside the why panel
    assert "ADX 34.6" in html
    assert "IVR 50" in html
    assert "+8.3%" in html                     # extension vs 200MA
    assert "price vs MA20 up" in html          # forecast reasons
    assert "$760" in html and "$740" in html   # call/put walls (R/S)
    assert "max pain" in html.lower()
    assert "selling a put spread" in html      # plain-English summary


def test_copilot_no_plan_shows_quiet_empty_state(client, app_modules, monkeypatch):
    _, web_app = app_modules
    _quiet_copilot_net(monkeypatch, web_app, plan=None)
    html = client.get("/copilot").text
    assert "No play yet" in html
