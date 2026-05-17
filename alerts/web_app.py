"""
alerts/web_app.py -- Per-alert FastAPI web app.

Mobile-friendly page for each trading alert, with three sections:
    1. Alert details (regime, indicators, entry/stop/target, R/R)
    2. Chat (Claude-powered trading coach for this specific trade)
    3. Journal (did you take it, notes, outcome, P&L)

Persistence:
    All alerts, journal entries, and chat history live in alert_store.db
    (SQLite). Nothing is stored on the file system.

Run standalone:
    uvicorn alerts.web_app:app --host 127.0.0.1 --port 8000 --reload

Routes:
    GET  /health                       -> uptime check
    GET  /                             -> recent alerts list
    GET  /alerts/{alert_id}            -> per-alert HTML page
    POST /alerts/{alert_id}/chat       -> chat endpoint  -> {"reply": str}
    POST /alerts/{alert_id}/journal    -> save journal entry
    GET  /alerts/{alert_id}/journal    -> journal entries as JSON
"""

from __future__ import annotations

import html
import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
from alerts import alert_store
from journal.trade_recorder import TradeRecorder
from signals import macro_runner

CLAUDE_MODEL = "claude-sonnet-4-6"


# ─────────────────────────────────────────
# APP
# ─────────────────────────────────────────

app = FastAPI(title="Trading Assistant - Alert Detail", docs_url=None, redoc_url=None)


# ─────────────────────────────────────────
# REQUEST MODELS
# ─────────────────────────────────────────

class ChatRequest(BaseModel):
    """Single chat turn from the web UI."""
    message: str


class JournalRequest(BaseModel):
    """Journal entry payload from the web UI."""
    took_trade:      bool
    direction_agree: bool = True
    notes:           str  = ""
    outcome:         str  = "open"
    pnl:             float | None = None


# ─────────────────────────────────────────
# CLAUDE SYSTEM PROMPT
# ─────────────────────────────────────────

# Static portion -- safe to cache across chat turns within a session.
_COACH_PREAMBLE = """You are an expert trading coach embedded in a personal trading assistant.
The trader has opened a specific alert on their phone and wants to discuss it before deciding.

Your role:
- Answer questions about THIS specific trade -- always reference the actual numbers in the alert.
- Help evaluate risk, timing, position sizing, and whether the edge is real.
- Explain what each indicator signal means in the current context.
- If asked for invalidation levels, give specific prices based on the alert data.
- Be direct and concise -- under 200 words unless the trader asks for detail.
- If the setup looks weak or the R/R is poor, say so honestly.
- Do not give generic trading advice -- stay specific to this alert.
- Do not tell the trader to "buy now" or make the decision for them.

Backtest context (5-year SPY daily replay):
- Iron condors in CHOPPY_LOW_VOL: 74.1% win rate (the core edge).
- Trending up calm:               38.5% win rate.
- Trending down calm:              44.7% win rate.
- TRENDING_HIGH_VOL is not tradeable (19% historical win rate).

Always remind the user to paper trade for 30 days before going live with real money."""


def _build_alert_context(alert: dict) -> str:
    """Per-alert dynamic context appended to the cached preamble."""
    full = alert.get("full_alert") if isinstance(alert.get("full_alert"), dict) else {}

    def pick(key: str, default: Any = "N/A") -> Any:
        return alert.get(key) or full.get(key) or default

    ticker     = pick("ticker", "?")
    direction  = pick("direction", "?")
    regime     = pick("regime", "?")
    play       = pick("play", "?")
    strategy   = pick("strategy", "")
    confidence = pick("confidence", "N/A")
    vix        = pick("vix")
    ivr        = pick("ivr")
    adx        = pick("adx")
    entry      = pick("entry")
    stop       = pick("stop")
    target     = pick("target")
    rr         = pick("rr_ratio")
    rsi        = full.get("rsi", "N/A")
    rvol       = full.get("rvol", "N/A")
    cvd        = full.get("cvd_slope", "N/A")
    ma20       = full.get("ma20", "N/A")
    ma50       = full.get("ma50", "N/A")
    ma200      = full.get("ma200", "N/A")
    tags       = full.get("setup_tags", []) or []
    full_card  = full.get("discord_message") or alert.get("discord_message", "")

    tags_text = "\n".join(f"  - {t}" for t in tags) if tags else "  None recorded"
    strat_label = (
        strategy.replace("_", " ").upper() if strategy
        else "Standard Swing / Intraday"
    )

    return f"""
=== ALERT CONTEXT ===========================
Ticker:      {ticker}
Direction:   {direction}
Regime:      {regime}
Play:        {play}
Strategy:    {strat_label}
Confidence:  {confidence}

Macro:
  VIX:  {vix}
  IVR:  {ivr}
  ADX:  {adx}

Trade Levels:
  Entry:   {entry}
  Stop:    {stop}
  Target:  {target}
  R/R:     {rr}

Indicators:
  RSI:        {rsi}
  RVOL:       {rvol}
  CVD slope:  {cvd}
  MA20: {ma20}   MA50: {ma50}   MA200: {ma200}

Setup Triggers:
{tags_text}

Full Alert Card:
{full_card}
=============================================
""".strip()


# ─────────────────────────────────────────
# CLAUDE CALL
# ─────────────────────────────────────────

def _ask_claude(alert: dict, user_message: str, history: list[dict]) -> str:
    """Synchronous one-shot chat call. Returns the assistant text reply."""
    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        return "ANTHROPIC_API_KEY is not configured -- chat is unavailable."

    # Imported lazily so unit tests can run without the SDK installed.
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    system = [
        {"type": "text", "text": _COACH_PREAMBLE,                 "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _build_alert_context(alert)},
    ]
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_message})

    try:
        resp = client.messages.create(
            model       = CLAUDE_MODEL,
            max_tokens  = 1024,
            system      = system,
            messages    = messages,
        )
        parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
        return "".join(parts).strip() or "(empty response)"
    except anthropic.AuthenticationError:
        return "Invalid ANTHROPIC_API_KEY -- check your .env file."
    except Exception as e:
        return f"Chat error: {e}"


# ─────────────────────────────────────────
# HTML  (single-string, no templates)
# ─────────────────────────────────────────

_NAV_CSS = """
.nav{position:sticky;top:0;z-index:10;background:#0d1117;
     border-bottom:1px solid #30363d;margin:-1rem -1rem 1rem;padding:.6rem 1rem;
     display:flex;gap:.4rem;overflow-x:auto;-webkit-overflow-scrolling:touch}
.nav a{flex:0 0 auto;color:#8b949e;text-decoration:none;padding:.35rem .75rem;
       border-radius:6px;font-size:.85rem;font-weight:500;white-space:nowrap}
.nav a:hover{color:#c9d1d9;background:#161b22}
.nav a.active{color:#fff;background:#1f6feb}
.pnl-pos{color:#3fb950}
.pnl-neg{color:#f85149}
.pnl-zero{color:#8b949e}
.status-open{background:#1f3a5f;color:#58a6ff;border:1px solid #1f6feb}
.status-win{background:#0f3a1f;color:#3fb950;border:1px solid #238636}
.status-loss{background:#3a1212;color:#f85149;border:1px solid #b62324}
.status-be{background:#3a2f0f;color:#d29922;border:1px solid #9e6a03}
.status-auto{background:#2d1b3d;color:#bc8cff;border:1px solid #6e40c9}
"""

_INDEX_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     background:#0d1117;color:#c9d1d9;line-height:1.5;padding:1rem;max-width:760px;margin:0 auto}
h1{font-size:1.4rem;margin-bottom:1rem;color:#58a6ff}
.alert-card{background:#161b22;border:1px solid #30363d;border-radius:8px;
            padding:.9rem 1rem;margin-bottom:.75rem;text-decoration:none;color:inherit;display:block}
.alert-card:hover{border-color:#58a6ff}
.alert-row{display:flex;justify-content:space-between;font-size:.9rem;margin-top:.25rem}
.muted{color:#8b949e;font-size:.85rem}
.badge{display:inline-block;padding:.1rem .5rem;border-radius:4px;font-size:.75rem;
       background:#21262d;border:1px solid #30363d;margin-right:.25rem}
.empty{text-align:center;color:#8b949e;padding:3rem 0}
""" + _NAV_CSS

_DETAIL_CSS = _INDEX_CSS + """
.section{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:1rem;margin-bottom:1rem}
.section h2{font-size:1rem;color:#58a6ff;margin-bottom:.75rem;
            text-transform:uppercase;letter-spacing:.05em}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:.5rem .75rem;font-size:.9rem}
.grid div span{color:#8b949e;display:block;font-size:.75rem;text-transform:uppercase}
.grid div b{font-weight:600;color:#c9d1d9}
.chat-box{height:280px;overflow-y:auto;padding:.5rem;background:#0d1117;
          border:1px solid #30363d;border-radius:6px;margin-bottom:.5rem;font-size:.9rem}
.msg{margin-bottom:.6rem;padding:.5rem .7rem;border-radius:6px;white-space:pre-wrap;word-wrap:break-word}
.msg.user{background:#1f3a5f;border-left:3px solid #58a6ff}
.msg.assistant{background:#21262d;border-left:3px solid #3fb950}
.row{display:flex;gap:.5rem}
textarea,input,select{width:100%;background:#0d1117;border:1px solid #30363d;
       border-radius:6px;padding:.5rem;color:#c9d1d9;font-family:inherit;font-size:.9rem}
textarea{resize:vertical;min-height:60px}
button{background:#238636;color:#fff;border:none;border-radius:6px;
       padding:.55rem 1rem;font-weight:600;cursor:pointer;font-size:.9rem}
button:hover{background:#2ea043}
button:disabled{background:#21262d;color:#8b949e;cursor:not-allowed}
.toggle{display:flex;gap:.5rem;margin:.4rem 0}
.toggle button{flex:1;background:#21262d;color:#c9d1d9;border:1px solid #30363d}
.toggle button.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
.entry{background:#0d1117;border:1px solid #30363d;border-radius:6px;
       padding:.5rem .7rem;margin-top:.5rem;font-size:.85rem}
.outcome-win{color:#3fb950}
.outcome-loss{color:#f85149}
.outcome-be{color:#d29922}
label{display:block;font-size:.8rem;color:#8b949e;margin-bottom:.2rem;
      text-transform:uppercase;letter-spacing:.04em}
.field{margin-bottom:.7rem}
"""


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else "—"))


_NAV_LINKS = [
    ("alerts",  "/",        "Alerts"),
    ("trades",  "/trades",  "Trades"),
    ("journal", "/journal", "Journal"),
    ("chats",   "/chats",   "Chats"),
    ("macro",   "/macro",   "Macro"),
]


def _render_nav(active: str) -> str:
    items = []
    for key, href, label in _NAV_LINKS:
        cls = "active" if key == active else ""
        items.append(f'<a href="{href}" class="{cls}">{label}</a>')
    return f'<nav class="nav">{"".join(items)}</nav>'


def _render_page(title: str, heading: str, body: str, css: str, active_nav: str) -> str:
    """Shared HTML wrapper: head + nav bar + page body."""
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head><body>
{_render_nav(active_nav)}
<h1>{html.escape(heading)}</h1>
{body}
</body></html>"""


def _render_index(alerts: list[dict]) -> str:
    """Recent-alerts list view."""
    if not alerts:
        body = '<div class="empty">No alerts yet. They appear here when scanners fire.</div>'
    else:
        rows = []
        for a in alerts:
            ticker    = _esc(a.get("ticker") or "SPY")
            regime    = _esc(a.get("regime") or "")
            play      = _esc(a.get("play") or a.get("strategy") or "")
            direction = _esc(a.get("direction") or "")
            created   = _esc((a.get("created_at") or "")[:19].replace("T", " "))
            rows.append(f'''
<a class="alert-card" href="/alerts/{html.escape(a["alert_id"])}">
  <div><b>{ticker}</b> &middot; {direction} &middot; {play}</div>
  <div class="alert-row">
    <span class="badge">{regime}</span>
    <span class="muted">{created} UTC</span>
  </div>
</a>''')
        body = "\n".join(rows)

    return _render_page(
        title       = "Trading Assistant - Alerts",
        heading     = "Recent Alerts",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "alerts",
    )


def _trade_status(trade: dict) -> tuple[str, str]:
    """(label, css class) for a trade outcome."""
    outcome = (trade.get("outcome") or "open").lower()
    if outcome == "win":       return ("WIN",      "status-win")
    if outcome == "loss":      return ("LOSS",     "status-loss")
    if outcome == "breakeven": return ("BE",       "status-be")
    return ("OPEN", "status-open")


def _render_trades(trades: list[dict]) -> str:
    """Cross-trade list view."""
    if not trades:
        body = '<div class="empty">No trades recorded yet.</div>'
    else:
        rows = []
        # Newest first — trades.json is naturally append order.
        for t in reversed(trades):
            ticker    = _esc(t.get("ticker") or "?")
            direction = _esc(t.get("direction") or "")
            strategy  = _esc((t.get("strategy") or t.get("trade_type") or "").replace("_", " "))
            entry_ts  = _esc((t.get("entry_timestamp") or t.get("entry_date") or "")[:19].replace("T", " "))
            pnl       = t.get("pnl_dollars")
            pnl_cls   = "pnl-pos" if (pnl or 0) > 0 else "pnl-neg" if (pnl or 0) < 0 else "pnl-zero"
            pnl_str   = f"${pnl:+,.2f}" if isinstance(pnl, (int, float)) else "—"
            label, status_cls = _trade_status(t)
            # AUTO-PAPER badge for bot-generated paper trades
            notes_entry = t.get("notes_entry") or ""
            auto_badge = (
                '<span class="badge status-auto">AUTO-PAPER</span>'
                if "[AUTO-PAPER]" in notes_entry else ""
            )
            rows.append(f'''
<div class="alert-card">
  <div><b>{ticker}</b> &middot; {direction} &middot; {strategy}</div>
  <div class="alert-row">
    <span>
      <span class="badge {status_cls}">{label}</span>
      {auto_badge}
    </span>
    <span class="{pnl_cls}"><b>{pnl_str}</b></span>
  </div>
  <div class="muted" style="margin-top:.25rem">{entry_ts}</div>
</div>''')
        body = "\n".join(rows)

    return _render_page(
        title       = "Trading Assistant - Trades",
        heading     = "Trade History",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "trades",
    )


def _render_journal(entries: list[dict]) -> str:
    """Cross-alert journal feed."""
    if not entries:
        body = '<div class="empty">No journal entries yet. Add one from any alert page.</div>'
    else:
        rows = []
        for j in entries:
            ticker  = _esc(j.get("ticker") or "—")
            regime  = _esc(j.get("regime") or "")
            took    = "Took" if j.get("took_trade") else "Skipped"
            outcome = (j.get("outcome") or "open").lower()
            _, status_cls = _trade_status({"outcome": outcome})
            pnl     = j.get("pnl")
            pnl_cls = "pnl-pos" if (pnl or 0) > 0 else "pnl-neg" if (pnl or 0) < 0 else "pnl-zero"
            pnl_str = f"${pnl:+,.2f}" if isinstance(pnl, (int, float)) else ""
            notes   = _esc((j.get("notes") or "")[:200])
            created = _esc((j.get("created_at") or "")[:19].replace("T", " "))
            aid     = html.escape(j.get("alert_id") or "")
            rows.append(f'''
<a class="alert-card" href="/alerts/{aid}">
  <div><b>{ticker}</b> &middot; {took} &middot; <span class="badge {status_cls}">{html.escape(outcome.upper())}</span>
       {f'<span class="{pnl_cls}"><b>{pnl_str}</b></span>' if pnl_str else ''}</div>
  <div class="muted" style="margin-top:.25rem">{notes}</div>
  <div class="alert-row">
    <span class="badge">{regime}</span>
    <span class="muted">{created} UTC</span>
  </div>
</a>''')
        body = "\n".join(rows)

    return _render_page(
        title       = "Trading Assistant - Journal",
        heading     = "Journal",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "journal",
    )


_FLAG_CLASS = {
    "calm":           "status-win",
    "cautious":       "status-be",
    "stress":         "status-loss",
    "extreme_stress": "status-loss",
    "unknown":        "status-open",
}

_SIGNAL_CLASS = {
    "trending_aligned": "status-win",
    "rotating":         "status-be",
    "dispersed":        "status-loss",
    "unknown":          "status-open",
}


def _render_macro(vix: dict | None, sector: dict | None) -> str:
    """Today's macro snapshot — VIX term structure + sector breadth."""

    # ── VIX section ────────────────────────────
    if vix:
        flag       = vix.get("flag") or "unknown"
        flag_cls   = _FLAG_CLASS.get(flag, "status-open")
        ratio      = vix.get("ratio")
        ratio_str  = f"{ratio:.3f}" if isinstance(ratio, (int, float)) else "—"
        asof_str   = _esc((vix.get("asof") or "")[:19].replace("T", " "))

        def _fmt(v): return f"{v:.2f}" if isinstance(v, (int, float)) else "—"

        vix_grid = (
            f'<div><span>VIX9D</span><b>{_fmt(vix.get("VIX9D"))}</b></div>'
            f'<div><span>VIX</span><b>{_fmt(vix.get("VIX"))}</b></div>'
            f'<div><span>VIX3M</span><b>{_fmt(vix.get("VIX3M"))}</b></div>'
            f'<div><span>VIX6M</span><b>{_fmt(vix.get("VIX6M"))}</b></div>'
        )
        vix_html = f'''
<div class="alert-card">
  <div><b>VIX Term Structure</b>
       <span class="badge {flag_cls}" style="margin-left:.5rem">{html.escape(flag.upper())}</span></div>
  <div class="muted" style="margin:.4rem 0">
    Contango ratio (VIX/VIX3M): <b>{ratio_str}</b>
    {"&middot; backwardation = stress" if isinstance(ratio, (int,float)) and ratio > 1.0 else ""}
  </div>
  <div class="grid">{vix_grid}</div>
  <div class="muted" style="margin-top:.5rem">{asof_str} UTC</div>
</div>'''
    else:
        vix_html = '<div class="empty">No VIX snapshot yet. Runs daily at 08:55 ET.</div>'

    # ── Sector section ──────────────────────────
    if sector:
        signal      = sector.get("signal") or "unknown"
        signal_cls  = _SIGNAL_CLASS.get(signal, "status-open")
        dispersion  = sector.get("dispersion")
        d_str       = f"{dispersion:.2f}" if isinstance(dispersion, (int, float)) else "—"
        leaders     = sector.get("leaders")  or []
        laggards    = sector.get("laggards") or []
        horizon     = sector.get("horizon") or 20
        s_asof      = _esc((sector.get("asof") or "")[:19].replace("T", " "))

        def _row(items, color_cls):
            return "".join(
                f'<div><span>{html.escape(t)}</span>'
                f'<b class="{color_cls}">{v:+.2f}</b></div>'
                for t, v in items
            )

        sector_html = f'''
<div class="alert-card">
  <div><b>Sector Breadth ({horizon}d RS vs SPY)</b>
       <span class="badge {signal_cls}" style="margin-left:.5rem">{html.escape(signal.upper())}</span></div>
  <div class="muted" style="margin:.4rem 0">Dispersion: <b>{d_str}</b></div>

  <div style="margin-top:.6rem"><span class="muted">Leaders</span></div>
  <div class="grid">{_row(leaders, "pnl-pos")}</div>

  <div style="margin-top:.6rem"><span class="muted">Laggards</span></div>
  <div class="grid">{_row(laggards, "pnl-neg")}</div>

  <div class="muted" style="margin-top:.5rem">{s_asof} UTC</div>
</div>'''
    else:
        sector_html = '<div class="empty">No sector snapshot yet. Runs daily at 10:00 ET.</div>'

    body = vix_html + sector_html
    return _render_page(
        title       = "Trading Assistant - Macro",
        heading     = "Macro Snapshot",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "macro",
    )


def _render_chats(threads: list[dict]) -> str:
    """Cross-alert chat threads list."""
    if not threads:
        body = '<div class="empty">No chats yet. Start one from any alert page.</div>'
    else:
        rows = []
        for c in threads:
            ticker  = _esc(c.get("ticker") or "—")
            regime  = _esc(c.get("regime") or "")
            count   = int(c.get("msg_count") or 0)
            last    = _esc((c.get("last_msg") or "")[:140])
            last_at = _esc((c.get("last_msg_at") or "")[:19].replace("T", " "))
            aid     = html.escape(c.get("alert_id") or "")
            rows.append(f'''
<a class="alert-card" href="/alerts/{aid}">
  <div><b>{ticker}</b> &middot; {count} message{'s' if count != 1 else ''}</div>
  <div class="muted" style="margin-top:.25rem">{last}</div>
  <div class="alert-row">
    <span class="badge">{regime}</span>
    <span class="muted">{last_at} UTC</span>
  </div>
</a>''')
        body = "\n".join(rows)

    return _render_page(
        title       = "Trading Assistant - Chats",
        heading     = "Chat Threads",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "chats",
    )


def _render_detail(alert: dict, journal: list[dict], chat: list[dict]) -> str:
    """Per-alert detail page."""
    aid = alert["alert_id"]
    full = alert.get("full_alert") if isinstance(alert.get("full_alert"), dict) else {}

    def pick(key: str) -> Any:
        return alert.get(key) or full.get(key)

    fields = [
        ("Ticker",     pick("ticker") or "SPY"),
        ("Regime",     pick("regime")),
        ("Play",       pick("play") or pick("strategy")),
        ("Direction",  pick("direction")),
        ("VIX",        pick("vix")),
        ("IVR",        pick("ivr")),
        ("ADX",        pick("adx")),
        ("Confidence", pick("confidence")),
        ("Entry",      pick("entry")),
        ("Stop",       pick("stop")),
        ("Target",     pick("target")),
        ("R/R",        pick("rr_ratio")),
    ]
    grid_html = "".join(
        f'<div><span>{html.escape(label)}</span><b>{_esc(value)}</b></div>'
        for label, value in fields
    )
    created = _esc((alert.get("created_at") or "")[:19].replace("T", " "))

    # Pre-render existing chat history server-side; new turns appended via JS.
    chat_html = "".join(
        f'<div class="msg {html.escape(m["role"])}">{html.escape(m["content"])}</div>'
        for m in chat
    )

    # Pre-render existing journal entries.
    if journal:
        jrows = []
        for j in journal:
            outcome = (j.get("outcome") or "open").lower()
            cls = ("outcome-win"  if outcome == "win"
                   else "outcome-loss" if outcome == "loss"
                   else "outcome-be" if outcome == "breakeven"
                   else "")
            took = "Took" if j.get("took_trade") else "Skipped"
            pnl  = j.get("pnl")
            pnl_str = f" &middot; P&amp;L {_esc(pnl)}" if pnl is not None else ""
            notes = _esc(j.get("notes") or "")
            jrows.append(
                f'<div class="entry"><b>{html.escape(took)}</b> &middot; '
                f'<span class="{cls}">{html.escape(outcome)}</span>{pnl_str}'
                f'<div class="muted" style="margin-top:.2rem">{notes}</div></div>'
            )
        journal_html = "".join(jrows)
    else:
        journal_html = '<div class="muted" style="margin-top:.5rem">No journal entries yet.</div>'

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alert {html.escape(aid)} - Trading Assistant</title>
<style>{_DETAIL_CSS}</style>
</head><body>
{_render_nav("alerts")}
<h1>Alert {html.escape(aid)}</h1>
<div class="muted" style="margin-bottom:1rem">{created} UTC</div>

<div class="section">
  <h2>Alert Details</h2>
  <div class="grid">{grid_html}</div>
</div>

<div class="section">
  <h2>Chat</h2>
  <div id="chat" class="chat-box">{chat_html}</div>
  <div class="row">
    <textarea id="msg" placeholder="Ask about this trade..."></textarea>
  </div>
  <div class="row" style="margin-top:.5rem;justify-content:flex-end">
    <button id="send">Send</button>
  </div>
</div>

<div class="section">
  <h2>Journal</h2>
  <div class="field">
    <label>Did you take this trade?</label>
    <div class="toggle">
      <button data-took="1" class="active">Yes</button>
      <button data-took="0">No</button>
    </div>
  </div>
  <div class="field">
    <label>Notes</label>
    <textarea id="notes" placeholder="Why you took it (or skipped it), what you saw..."></textarea>
  </div>
  <div class="field">
    <label>Outcome</label>
    <select id="outcome">
      <option value="open" selected>Still Open</option>
      <option value="win">Win</option>
      <option value="loss">Loss</option>
      <option value="breakeven">Breakeven</option>
    </select>
  </div>
  <div class="field">
    <label>P&amp;L</label>
    <input id="pnl" type="number" step="0.01" placeholder="0.00">
  </div>
  <button id="save-journal">Save entry</button>

  <div id="journal-list" style="margin-top:1rem">{journal_html}</div>
</div>

<script>
const ALERT_ID = {aid!r};
const $ = (s) => document.querySelector(s);

// ── Chat ──
const chatBox = $("#chat");
const msgInput = $("#msg");
const sendBtn = $("#send");

function appendMsg(role, content) {{
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = content;
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}}

async function sendMessage() {{
  const text = msgInput.value.trim();
  if (!text) return;
  sendBtn.disabled = true;
  appendMsg("user", text);
  msgInput.value = "";
  try {{
    const r = await fetch(`/alerts/${{ALERT_ID}}/chat`, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ message: text }}),
    }});
    const data = await r.json();
    appendMsg("assistant", data.reply || data.error || "(no response)");
  }} catch (e) {{
    appendMsg("assistant", "Network error: " + e);
  }} finally {{
    sendBtn.disabled = false;
  }}
}}
sendBtn.addEventListener("click", sendMessage);
msgInput.addEventListener("keydown", (e) => {{
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendMessage();
}});
chatBox.scrollTop = chatBox.scrollHeight;

// ── Journal toggle ──
let tookTrade = 1;
document.querySelectorAll(".toggle button").forEach((btn) => {{
  btn.addEventListener("click", () => {{
    document.querySelectorAll(".toggle button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    tookTrade = parseInt(btn.dataset.took, 10);
  }});
}});

// ── Save journal entry ──
const journalBtn = $("#save-journal");
journalBtn.addEventListener("click", async () => {{
  journalBtn.disabled = true;
  const payload = {{
    took_trade:      !!tookTrade,
    direction_agree: true,
    notes:           $("#notes").value,
    outcome:         $("#outcome").value,
    pnl:             parseFloat($("#pnl").value) || null,
  }};
  try {{
    const r = await fetch(`/alerts/${{ALERT_ID}}/journal`, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(payload),
    }});
    if (r.ok) {{
      const list = await fetch(`/alerts/${{ALERT_ID}}/journal`).then(x => x.json());
      renderJournal(list);
      $("#notes").value = "";
      $("#pnl").value = "";
    }}
  }} catch (e) {{
    alert("Save failed: " + e);
  }} finally {{
    journalBtn.disabled = false;
  }}
}});

function renderJournal(items) {{
  const target = $("#journal-list");
  if (!items || !items.length) {{
    target.innerHTML = '<div class="muted" style="margin-top:.5rem">No journal entries yet.</div>';
    return;
  }}
  target.innerHTML = items.map(j => {{
    const outcome = (j.outcome || "open").toLowerCase();
    const cls = outcome === "win" ? "outcome-win"
              : outcome === "loss" ? "outcome-loss"
              : outcome === "breakeven" ? "outcome-be" : "";
    const took = j.took_trade ? "Took" : "Skipped";
    const pnlStr = (j.pnl !== null && j.pnl !== undefined) ? ` &middot; P&L ${{j.pnl}}` : "";
    return `<div class="entry"><b>${{took}}</b> &middot; <span class="${{cls}}">${{outcome}}</span>${{pnlStr}}<div class="muted" style="margin-top:.2rem">${{(j.notes || "").replace(/</g,"&lt;")}}</div></div>`;
  }}).join("");
}}
</script>
</body></html>"""


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────

@app.get("/health")
def health():
    """Health check."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    """Recent alerts list."""
    alerts = alert_store.get_recent_alerts(limit=20)
    return HTMLResponse(_render_index(alerts))


@app.get("/trades", response_class=HTMLResponse)
def trades_page():
    """Cross-trade history from TradeRecorder."""
    trades = TradeRecorder().get_all_trades()
    return HTMLResponse(_render_trades(trades))


@app.get("/journal", response_class=HTMLResponse)
def journal_page():
    """Cross-alert journal feed."""
    entries = alert_store.get_all_journal_entries(limit=50)
    return HTMLResponse(_render_journal(entries))


@app.get("/chats", response_class=HTMLResponse)
def chats_page():
    """List of alerts that have any chat history, newest activity first."""
    threads = alert_store.get_alerts_with_chat(limit=50)
    return HTMLResponse(_render_chats(threads))


@app.get("/macro", response_class=HTMLResponse)
def macro_page():
    """VIX term structure + sector breadth snapshot."""
    vix    = macro_runner.get_latest_vix()
    sector = macro_runner.get_latest_sector()
    return HTMLResponse(_render_macro(vix, sector))


@app.get("/alerts/{alert_id}", response_class=HTMLResponse)
def alert_page(alert_id: str):
    """Per-alert detail page."""
    alert = alert_store.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    journal = alert_store.get_journal_entries(alert_id)
    chat    = alert_store.get_chat_history(alert_id)
    return HTMLResponse(_render_detail(alert, journal, chat))


@app.post("/alerts/{alert_id}/chat")
def chat(alert_id: str, body: ChatRequest):
    """Send a message to the trading-coach Claude. Persists both turns."""
    alert = alert_store.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    user_msg = (body.message or "").strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="message required")

    history = alert_store.get_chat_history(alert_id)
    alert_store.save_chat_message(alert_id, "user", user_msg)
    reply = _ask_claude(alert, user_msg, history)
    alert_store.save_chat_message(alert_id, "assistant", reply)
    return JSONResponse({"reply": reply})


@app.post("/alerts/{alert_id}/journal")
def journal_save(alert_id: str, body: JournalRequest):
    """Save a journal entry for this alert."""
    if not alert_store.get_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    ok = alert_store.save_journal_entry(alert_id, body.model_dump())
    if not ok:
        raise HTTPException(status_code=500, detail="journal save failed")
    return JSONResponse({"ok": True})


@app.get("/alerts/{alert_id}/journal")
def journal_list(alert_id: str):
    """All journal entries for this alert."""
    if not alert_store.get_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return JSONResponse(alert_store.get_journal_entries(alert_id))
