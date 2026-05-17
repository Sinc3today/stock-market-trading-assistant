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

from fastapi import FastAPI, HTTPException, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
from loguru import logger
from alerts import alert_store
from alerts.macro_chat import MacroChat
from journal.trade_recorder import TradeRecorder
from journal.plan_logger import PlanLogger
from signals import macro_runner
from data.earnings_calendar import EarningsCalendar
from data import backtest_summary
from learning.portfolio_greeks import PortfolioGreeks

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


class MacroChatRequest(BaseModel):
    """Single chat turn for the macro-aware /chat route."""
    message: str


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
/* ── Sticky header with brand + groupable links ────────── */
.nav{position:sticky;top:0;z-index:10;background:#0d1117;
     border-bottom:1px solid #30363d;margin:-1rem -1rem 1rem;padding:.55rem 1rem;
     display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}
.nav-brand{color:#58a6ff;text-decoration:none;font-weight:700;font-size:.95rem;
           white-space:nowrap;letter-spacing:.02em}
.nav-toggle-input{display:none}
.nav-toggle{display:none;background:transparent;border:1px solid #30363d;
            color:#c9d1d9;border-radius:6px;padding:.35rem .65rem;cursor:pointer;
            font-size:1.1rem;line-height:1;user-select:none;-webkit-user-select:none}
.nav-toggle:hover{border-color:#58a6ff}
.nav-links{display:flex;align-items:center;gap:.4rem;flex:1;flex-wrap:wrap;
           margin-left:auto}
.nav-group{display:flex;align-items:center;gap:.25rem;padding:0 .35rem;
           border-left:1px solid #21262d}
.nav-group:first-child{border-left:none;padding-left:0}
.nav-group-label{display:none;color:#6e7681;font-size:.7rem;text-transform:uppercase;
                 letter-spacing:.06em;margin-right:.25rem}
.nav a:not(.nav-brand){flex:0 0 auto;color:#8b949e;text-decoration:none;
       padding:.4rem .7rem;border-radius:6px;font-size:.85rem;font-weight:500;
       white-space:nowrap}
.nav a:not(.nav-brand):hover{color:#c9d1d9;background:#161b22}
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

/* /levels ticker picker */
.lvl-picker{display:flex;gap:.5rem;align-items:center;padding:.6rem .8rem}
.lvl-picker select{flex:1;background:#0d1117;color:#c9d1d9;
                   border:1px solid #30363d;border-radius:6px;padding:.4rem .5rem}
.lvl-picker button{background:#1f6feb;color:#fff;border:none;border-radius:6px;
                   padding:.45rem 1rem;cursor:pointer;font-weight:600}
.lvl-picker button:hover{background:#388bfd}

/* ── Mobile: hamburger nav, single-col grids, bigger tap targets */
@media (max-width:760px){
  body{padding:.6rem}
  h1{font-size:1.2rem;margin-bottom:.6rem}
  .alert-card{padding:.7rem .8rem}
  .nav{margin:-.6rem -.6rem .8rem;padding:.5rem .6rem;flex-wrap:nowrap}
  .nav-toggle{display:inline-block}
  .nav-links{display:none;flex:1 0 100%;flex-direction:column;align-items:stretch;
             gap:.25rem;margin-top:.6rem;margin-left:0;
             border-top:1px solid #21262d;padding-top:.6rem}
  .nav-toggle-input:checked ~ .nav-links{display:flex}
  .nav-group{flex-direction:column;align-items:stretch;gap:.15rem;padding:.4rem 0;
             border-left:none;border-top:1px solid #161b22}
  .nav-group:first-child{border-top:none;padding-top:0}
  .nav-group-label{display:block;padding:0 .5rem .15rem}
  .nav a:not(.nav-brand){padding:.65rem .8rem;font-size:.95rem}   /* ~44px tall */
  .grid{grid-template-columns:1fr !important;gap:.4rem !important}
  .row{flex-direction:column}
  #lvl-chart{height:340px !important}
}
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


_NAV_GROUPS = [
    ("Now", [
        ("today",  "/today",  "Today"),
        ("levels", "/levels", "Levels"),
        ("macro",  "/macro",  "Macro"),
        ("alerts", "/",       "Alerts"),
    ]),
    ("Trades", [
        ("trades",  "/trades",  "Trades"),
        ("journal", "/journal", "Journal"),
        ("chats",   "/chats",   "Chats"),
    ]),
    ("Tools", [
        ("chat",     "/chat",     "Chat"),
        ("backtest", "/backtest", "Backtest"),
    ]),
]


def _render_nav(active: str) -> str:
    groups_html = []
    for label, items in _NAV_GROUPS:
        links = []
        for key, href, text in items:
            cls = "active" if key == active else ""
            links.append(f'<a href="{href}" class="{cls}">{text}</a>')
        groups_html.append(
            f'<div class="nav-group">'
            f'<span class="nav-group-label">{label}</span>'
            f'{"".join(links)}'
            f'</div>'
        )
    return (
        f'<nav class="nav">'
        f'<a href="/today" class="nav-brand">📊 SMTA</a>'
        f'<input type="checkbox" id="nav-toggle" class="nav-toggle-input">'
        f'<label for="nav-toggle" class="nav-toggle" aria-label="Open menu">☰</label>'
        f'<div class="nav-links">{"".join(groups_html)}</div>'
        f'</nav>'
    )


def _render_page(
    title: str, heading: str, body: str, css: str, active_nav: str,
    extra_head: str = "",
) -> str:
    """Shared HTML wrapper: head + nav bar + page body."""
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
{extra_head}
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


_VERDICT_CLASS = {
    "accepted":     "status-win",
    "rejected":     "status-loss",
    "inconclusive": "status-be",
    "pending":      "status-open",
}


def _render_backtest(
    stats: dict,
    hypotheses: dict,
    accuracy: dict,
    kb_groups: list[dict],
) -> str:
    """Backtest dashboard — what the bot has measured about itself."""
    # ── Overview card ──────────────────────────
    ov  = stats.get("overview")  or {}
    src = _esc(stats.get("source") or "?")
    sharpe = ov.get("sharpe")
    wr     = ov.get("win_rate_pct")
    sharpe_str = f"{sharpe:.2f}" if isinstance(sharpe, (int, float)) else "—"
    wr_str     = f"{wr:.1f}%"    if isinstance(wr, (int, float))     else "—"

    overview_html = f'''
<div class="alert-card">
  <div><b>Production Baseline</b>
       <span class="muted" style="float:right;font-size:.75rem">{src}</span></div>
  <div class="grid" style="margin-top:.5rem">
    <div><span>Sharpe (annual)</span><b>{sharpe_str}</b></div>
    <div><span>Win Rate</span><b>{wr_str}</b></div>
    <div><span>Years backtested</span><b>{_esc(stats.get("years"))}</b></div>
    <div><span>Version</span><b>{_esc(stats.get("version"))}</b></div>
  </div>
</div>'''

    # ── Per-regime ─────────────────────────────
    regime_rows = []
    for r in stats.get("by_regime") or []:
        wr_r  = r.get("win_rate_pct")
        wr_r_str = f"{wr_r:.1f}%" if isinstance(wr_r, (int, float)) else "—"
        tradeable = r.get("tradeable", True)
        badge_cls = "status-win" if tradeable else "status-loss"
        badge_txt = "TRADED" if tradeable else "SKIPPED"
        wr_cls = (
            "pnl-pos" if (isinstance(wr_r, (int, float)) and wr_r >= 60)
            else "pnl-neg" if (isinstance(wr_r, (int, float)) and wr_r <  35)
            else "pnl-zero"
        )
        regime_rows.append(f'''
<div style="padding:.45rem 0;border-bottom:1px solid #21262d">
  <div style="display:flex;justify-content:space-between">
    <span><b>{_esc(r.get("regime"))}</b>
          <span class="badge {badge_cls}" style="font-size:.65rem;margin-left:.4rem">{badge_txt}</span></span>
    <span class="{wr_cls}"><b>{wr_r_str}</b></span>
  </div>
  <div class="muted" style="margin-top:.15rem">{_esc(r.get("note") or "")}</div>
</div>''')

    regime_html = f'''
<div class="alert-card">
  <div><b>By Regime</b></div>
  <div style="margin-top:.4rem">{"".join(regime_rows) or '<div class="muted">No regime data.</div>'}</div>
</div>'''

    # ── Hypotheses ─────────────────────────────
    def _hypo_section(verdict: str, items: list[dict]) -> str:
        if not items:
            return ""
        cls  = _VERDICT_CLASS.get(verdict, "status-open")
        rows = []
        for spec in items[:5]:   # cap at 5 per bucket
            sharpe_delta = spec.get("sharpe_delta")
            pnl_delta    = spec.get("pnl_delta")
            sd = f"{sharpe_delta:+.2f}" if isinstance(sharpe_delta, (int, float)) else "—"
            pd = f"${pnl_delta:+,.0f}"  if isinstance(pnl_delta, (int, float))    else "—"
            rows.append(
                f'<div style="padding:.4rem 0;border-bottom:1px solid #21262d">'
                f'<div><b>{_esc(spec.get("var") or spec.get("id"))}</b> '
                f'<span class="muted">→ {_esc(spec.get("proposed_value"))}</span></div>'
                f'<div class="muted" style="margin-top:.15rem;font-size:.8rem">'
                f'ΔSharpe {sd} · ΔP&amp;L {pd}</div>'
                f'<div class="muted" style="margin-top:.15rem">{_esc((spec.get("rationale") or "")[:240])}</div>'
                f'</div>'
            )
        return (
            f'<div class="alert-card">'
            f'<div><b>Hypotheses — {verdict.title()}</b> '
            f'<span class="badge {cls}" style="margin-left:.4rem">{len(items)}</span></div>'
            f'<div style="margin-top:.4rem">{"".join(rows)}</div>'
            f'</div>'
        )

    hypo_html = "".join(
        _hypo_section(v, hypotheses.get(v, []))
        for v in ("accepted", "pending", "inconclusive", "rejected")
    )
    if not hypo_html:
        hypo_html = (
            '<div class="alert-card">'
            '<div><b>Hypotheses</b></div>'
            '<div class="muted" style="margin-top:.5rem">'
            'No hypotheses yet. The Saturday weekly job produces one '
            'per week — `logs/learning/hypotheses/` is empty.</div>'
            '</div>'
        )

    # ── Prediction accuracy ────────────────────
    acc_n = accuracy.get("sample") or 0
    acc_p = accuracy.get("accuracy")
    acc_p_str = f"{acc_p:.1f}%" if isinstance(acc_p, (int, float)) else "—"
    acc_cls = (
        "pnl-pos" if (isinstance(acc_p, (int, float)) and acc_p >= 60)
        else "pnl-neg" if (isinstance(acc_p, (int, float)) and acc_p < 40 and acc_n > 0)
        else "pnl-zero"
    )
    acc_html = f'''
<div class="alert-card">
  <div><b>Prediction Accuracy</b></div>
  <div class="grid" style="margin-top:.5rem">
    <div><span>Last 60 days</span><b class="{acc_cls}">{acc_p_str}</b></div>
    <div><span>Resolved sample</span><b>{acc_n}</b></div>
  </div>
</div>'''

    # ── KB observations ────────────────────────
    if kb_groups:
        kb_rows = []
        for g in kb_groups:
            kb_rows.append(
                f'<div style="padding:.45rem 0;border-bottom:1px solid #21262d">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<b>{_esc(g.get("category"))}</b>'
                f'<span class="badge">{g.get("count")}</span></div>'
                f'<div class="muted" style="margin-top:.15rem">{_esc(g.get("latest_claim"))}</div>'
                f'<div class="muted" style="font-size:.75rem">latest: {_esc(g.get("latest_date"))}</div>'
                f'</div>'
            )
        kb_html = (
            f'<div class="alert-card">'
            f'<div><b>KB Observations (last 30d)</b></div>'
            f'<div style="margin-top:.4rem">{"".join(kb_rows)}</div>'
            f'</div>'
        )
    else:
        kb_html = (
            '<div class="alert-card">'
            '<div><b>KB Observations</b></div>'
            '<div class="muted" style="margin-top:.5rem">'
            'No KB entries in the last 30 days yet.</div>'
            '</div>'
        )

    body = overview_html + regime_html + acc_html + hypo_html + kb_html
    return _render_page(
        title       = "Trading Assistant - Backtest",
        heading     = "Backtest & Self-Learning",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "backtest",
    )


def _render_macro_chat(history: list[dict], context_summary: str) -> str:
    """Macro-aware chat interface — full daily context, persistent history."""
    chat_html = "".join(
        f'<div class="msg {html.escape(m["role"])}">{html.escape(m["content"])}</div>'
        for m in history
    )
    if not chat_html:
        chat_html = (
            '<div class="muted" style="text-align:center;padding:1.5rem">'
            'Ask anything about today\'s setup, recent trades, or the KB.<br>'
            'Examples: "Should I take today\'s play given the events?" · '
            '"What happened the last 3 times we saw this regime?" · '
            '"How did the last 5 iron condors go?"'
            '</div>'
        )

    body = f'''
<div class="alert-card">
  <div class="muted" style="text-transform:uppercase;font-size:.7rem;margin-bottom:.3rem">
    Context Claude sees
  </div>
  <div class="muted" style="font-size:.8rem;font-family:monospace;line-height:1.4">
    {html.escape(context_summary)}
  </div>
</div>

<div class="section">
  <h2>Conversation</h2>
  <div id="chat" class="chat-box" style="height:380px">{chat_html}</div>
  <div class="row">
    <textarea id="msg" placeholder="Ask about today, recent trades, the KB..."></textarea>
  </div>
  <div class="row" style="margin-top:.5rem;justify-content:space-between">
    <button id="reset" style="background:#21262d;color:#c9d1d9">Reset</button>
    <button id="send">Send</button>
  </div>
</div>

<script>
const $ = (s) => document.querySelector(s);
const chatBox = $("#chat");
const msgInput = $("#msg");
const sendBtn = $("#send");
const resetBtn = $("#reset");

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
  // Clear the empty placeholder if visible
  if (chatBox.querySelector(".muted")) chatBox.innerHTML = "";
  appendMsg("user", text);
  msgInput.value = "";
  try {{
    const r = await fetch("/chat", {{
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

resetBtn.addEventListener("click", async () => {{
  if (!confirm("Clear chat history?")) return;
  await fetch("/chat/reset", {{ method: "POST" }});
  location.reload();
}});

chatBox.scrollTop = chatBox.scrollHeight;
</script>'''

    return _render_page(
        title       = "Trading Assistant - Chat",
        heading     = "Macro Chat",
        body        = body,
        css         = _DETAIL_CSS,
        active_nav  = "chat",
    )


# ── Plain-English label maps ─────────────────────────
# Keep technical names accessible (regimes, flags) but always render the
# user-facing version. New mappings go here so /today, /macro, and any
# future page share the same vocabulary.

_REGIME_LABEL = {
    "trending_up_calm":   "Steady uptrend, low volatility",
    "trending_down_calm": "Steady downtrend, low volatility",
    "choppy_low_vol":     "Sideways market, low volatility",
    "choppy_high_vol":    "Sideways market, high volatility",
    "trending_high_vol":  "Trending market, high volatility",
}

_VIX_FLAG_LABEL = {
    "calm":           "Low (market expects calm)",
    "cautious":       "Rising (some near-term jitters)",
    "stress":         "High (fear rising)",
    "extreme_stress": "Extreme (vol event likely)",
    "unknown":        "Unknown",
}

_SECTOR_SIGNAL_LABEL = {
    "trending_aligned": "All sectors moving together",
    "rotating":         "Some sectors leading, others lagging",
    "dispersed":        "Heavy rotation (sideways-friendly)",
    "unknown":          "Unknown",
}


def _regime_label(regime: str | None) -> str:
    if not regime:
        return "?"
    return _REGIME_LABEL.get(regime.lower(), regime.replace("_", " ").title())


def _render_sparkline_svg(closes: list[float], width: int = 320, height: int = 60) -> str:
    """
    Render a tiny inline SVG sparkline from a list of closes. No external
    deps. Returns "" when there's not enough data to draw a line.
    """
    if not closes or len(closes) < 2:
        return ""
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    n = len(closes)
    points = []
    for i, c in enumerate(closes):
        x = round(i * (width - 4) / (n - 1) + 2, 2)
        y = round((height - 4) - (c - lo) / span * (height - 6) + 2, 2)
        points.append(f"{x},{y}")
    last_y = points[-1].split(",")[1]
    color  = "#3fb950" if closes[-1] >= closes[0] else "#f85149"
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="none" style="display:block">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
        f'points="{" ".join(points)}"/>'
        f'<circle cx="{points[-1].split(",")[0]}" cy="{last_y}" r="2.5" '
        f'fill="{color}"/></svg>'
    )


def _render_spy_thumbnail(spy_closes: list[float]) -> str:
    """Tiny SPY card for /today — sparkline + current price + 30d change %.
    Links to the full /levels/SPY view."""
    if not spy_closes or len(spy_closes) < 2:
        return ""
    last  = spy_closes[-1]
    first = spy_closes[0]
    pct   = (last - first) / first * 100 if first else 0
    pct_cls = "pnl-pos" if pct >= 0 else "pnl-neg"
    spark = _render_sparkline_svg(spy_closes)
    return f'''
<a class="alert-card" href="/levels/SPY" style="display:block;text-decoration:none">
  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.3rem">
    <span><b>SPY</b> <span class="muted" style="font-size:.75rem">last {len(spy_closes)}d</span></span>
    <span><b>${last:,.2f}</b> <span class="{pct_cls}" style="font-size:.85rem">({pct:+.2f}%)</span></span>
  </div>
  {spark}
  <div class="muted" style="font-size:.75rem;margin-top:.3rem">Tap for full chart + levels</div>
</a>'''


def _fetch_spy_closes_for_today(days: int = 30) -> list[float]:
    """Best-effort SPY-closes fetch for the /today thumbnail.
    Returns [] on any failure — caller renders nothing."""
    try:
        from data.polygon_client import PolygonClient
        df = PolygonClient().get_bars(
            "SPY", timeframe=config.SWING_PRIMARY_TIMEFRAME,
            limit=days + 5, days_back=days * 2 + 10,
        )
        if df is None or len(df) == 0:
            return []
        col = next(c for c in df.columns if c.lower() == "close")
        return [float(v) for v in df[col].tail(days)]
    except Exception as e:
        logger.warning(f"/today sparkline: SPY fetch failed: {e}")
        return []


def _render_today(plan: dict | None, spy_closes: list[float] | None = None) -> str:
    """Today's morning brief: regime, play, narrative, skip/watch conditions."""
    if not plan:
        body = (
            '<div class="empty">'
            'No morning brief yet for today.<br>'
            'The 09:15 ET job will produce one on the next trading day.'
            '</div>'
        )
        return _render_page(
            title="Trading Assistant - Today",
            heading="Today's Play",
            body=body,
            css=_INDEX_CSS,
            active_nav="today",
        )

    regime_raw   = plan.get("regime") or "?"
    regime_plain = _esc(_regime_label(regime_raw))
    play         = _esc(plan.get("play") or plan.get("action") or "—")
    strategy     = _esc(plan.get("strategy") or "—")
    rr           = _esc(plan.get("rr_ratio") or "—")
    dte          = _esc(plan.get("recommended_dte") or "—")
    max_p        = plan.get("max_profit")
    max_l        = plan.get("max_loss")
    max_p_str    = f"${max_p:,.0f}" if isinstance(max_p, (int, float)) else "—"
    max_l_str    = f"${max_l:,.0f}" if isinstance(max_l, (int, float)) else "—"
    # Prefer the plain_summary field for the thesis; fall back to the
    # more technical narrative if we don't have one yet.
    summary      = _esc(plan.get("plain_summary") or plan.get("narrative") or "")
    skips        = plan.get("skip_conditions") or []
    watches      = plan.get("watch_conditions") or []
    exit_rule    = _esc(plan.get("exit_rule") or "—")
    macro        = plan.get("macro_context") or {}
    vix_ts       = macro.get("vix_ts") or {}
    sector       = macro.get("sector") or {}
    events       = macro.get("events") or []
    plan_date    = _esc(plan.get("date") or "")

    is_skip    = (plan.get("action") == "SKIP") or not plan.get("strategy")
    regime_cls = "status-loss" if is_skip else "status-win"

    play_html = f'''
<div class="alert-card">
  <div style="margin-bottom:.6rem">
    <span class="badge {regime_cls}" style="font-size:.85rem">{regime_plain}</span>
    <span class="muted" style="float:right">{plan_date}</span>
  </div>
  <div style="font-size:1.1rem;margin-bottom:.4rem"><b>{play}</b></div>
  <div class="grid">
    <div><span>Strategy</span><b>{strategy}</b></div>
    <div><span>Risk / Reward</span><b>{rr}</b></div>
    <div><span>Days to expiry</span><b>{dte}</b></div>
    <div><span>Max win / loss</span><b>{max_p_str} / {max_l_str}</b></div>
  </div>
  <div class="muted" style="margin-top:.5rem">Exit: {exit_rule}</div>
</div>'''

    summary_html = (
        f'<div class="alert-card"><div class="muted" style="text-transform:uppercase;'
        f'font-size:.75rem;margin-bottom:.4rem">Why this trade today</div>'
        f'<div>{summary}</div></div>'
        if summary else ""
    )

    def _condition_card(label, items, css_cls):
        if not items:
            return ""
        rows = "".join(
            f'<div style="padding:.35rem 0;border-bottom:1px solid #21262d">• {_esc(s)}</div>'
            for s in items
        )
        return f'''
<div class="alert-card">
  <div class="muted" style="text-transform:uppercase;font-size:.75rem;margin-bottom:.4rem">
    <span class="badge {css_cls}" style="font-size:.7rem">{label}</span>
  </div>
  {rows}
</div>'''

    skip_html  = _condition_card("Skip this trade if",  skips,   "status-loss")
    watch_html = _condition_card("Watch for",            watches, "status-be")

    # Market conditions summary — plain English labels
    macro_bits = []
    flag = vix_ts.get("flag")
    if flag:
        macro_bits.append(
            f'<div><span>Volatility</span>'
            f'<b>{_esc(_VIX_FLAG_LABEL.get(flag, flag))}</b></div>'
        )
    signal = sector.get("signal")
    if signal:
        macro_bits.append(
            f'<div><span>Sector strength</span>'
            f'<b>{_esc(_SECTOR_SIGNAL_LABEL.get(signal, signal))}</b></div>'
        )
    if events:
        events_str = ", ".join(
            f"{_esc(e.get('event'))} ({_esc(e.get('days_away'))}d)" for e in events
        )
        macro_bits.append(f'<div><span>Events next 48h</span><b>{events_str}</b></div>')

    macro_html = ""
    if macro_bits:
        macro_html = f'''
<div class="alert-card">
  <div class="muted" style="text-transform:uppercase;font-size:.75rem;margin-bottom:.4rem">
    Market conditions
  </div>
  <div class="grid">{"".join(macro_bits)}</div>
</div>'''

    spy_thumb = _render_spy_thumbnail(spy_closes or [])

    body = play_html + spy_thumb + summary_html + skip_html + watch_html + macro_html
    return _render_page(
        title       = "Trading Assistant - Today",
        heading     = "Today's Play",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "today",
    )


def _render_macro(vix: dict | None, sector: dict | None,
                  earnings: list[dict] | None = None,
                  greeks: dict | None = None) -> str:
    """Today's macro snapshot — VIX term structure + sector breadth."""

    # ── Volatility section ──────────────────────
    if vix:
        flag       = vix.get("flag") or "unknown"
        flag_cls   = _FLAG_CLASS.get(flag, "status-open")
        flag_plain = _VIX_FLAG_LABEL.get(flag, flag)
        ratio      = vix.get("ratio")
        ratio_str  = f"{ratio:.3f}" if isinstance(ratio, (int, float)) else "—"
        asof_str   = _esc((vix.get("asof") or "")[:19].replace("T", " "))

        def _fmt(v): return f"{v:.2f}" if isinstance(v, (int, float)) else "—"

        # Plain interpretation of the ratio
        if isinstance(ratio, (int, float)):
            if ratio > 1.10:
                ratio_explain = "Traders pricing in fear (volatility likely rising)"
            elif ratio > 1.00:
                ratio_explain = "Slight near-term jitters"
            elif ratio < 0.90:
                ratio_explain = "Market expects extended calm"
            else:
                ratio_explain = "Market expects calm"
        else:
            ratio_explain = ""

        vix_grid = (
            f'<div><span>Today</span><b>{_fmt(vix.get("VIX"))}</b></div>'
            f'<div><span>1-week expectation</span><b>{_fmt(vix.get("VIX9D"))}</b></div>'
            f'<div><span>3-month expectation</span><b>{_fmt(vix.get("VIX3M"))}</b></div>'
            f'<div><span>6-month expectation</span><b>{_fmt(vix.get("VIX6M"))}</b></div>'
        )
        vix_html = f'''
<div class="alert-card">
  <div><b>Market Volatility</b>
       <span class="badge {flag_cls}" style="margin-left:.5rem">{html.escape(flag_plain)}</span></div>
  <div class="muted" style="margin:.4rem 0">{html.escape(ratio_explain)}</div>
  <div class="grid">{vix_grid}</div>
  <div class="muted" style="margin-top:.5rem">Updated {asof_str} UTC</div>
</div>'''
    else:
        vix_html = '<div class="empty">No volatility snapshot yet. Runs daily at 08:55 ET.</div>'

    # ── Sectors section ─────────────────────────
    if sector:
        signal      = sector.get("signal") or "unknown"
        signal_cls  = _SIGNAL_CLASS.get(signal, "status-open")
        signal_plain = _SECTOR_SIGNAL_LABEL.get(signal, signal)
        leaders     = sector.get("leaders")  or []
        laggards    = sector.get("laggards") or []
        horizon     = sector.get("horizon") or 20
        s_asof      = _esc((sector.get("asof") or "")[:19].replace("T", " "))

        def _row(items, color_cls):
            return "".join(
                f'<div><span>{html.escape(t)}</span>'
                f'<b class="{color_cls}">{v:+.2f}%</b></div>'
                for t, v in items
            )

        sector_html = f'''
<div class="alert-card">
  <div><b>Sector Strength (vs market, last {horizon} days)</b>
       <span class="badge {signal_cls}" style="margin-left:.5rem">{html.escape(signal_plain)}</span></div>

  <div style="margin-top:.6rem"><span class="muted">Outperforming the market</span></div>
  <div class="grid">{_row(leaders, "pnl-pos")}</div>

  <div style="margin-top:.6rem"><span class="muted">Underperforming the market</span></div>
  <div class="grid">{_row(laggards, "pnl-neg")}</div>

  <div class="muted" style="margin-top:.5rem">Updated {s_asof} UTC</div>
</div>'''
    else:
        sector_html = '<div class="empty">No sector snapshot yet. Runs daily at 10:00 ET.</div>'

    # ── Earnings panel ───────────────────────────
    if earnings:
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:.4rem 0;border-bottom:1px solid #21262d">'
            f'<span><b>{_esc(e.get("ticker"))}</b></span>'
            f'<span class="muted">{_esc(e.get("earnings_date"))} '
            f'<span class="badge" style="margin-left:.4rem">{_esc(e.get("days_away"))}d</span>'
            f'</span></div>'
            for e in earnings
        )
        earnings_html = f'''
<div class="alert-card">
  <div><b>Watchlist Earnings (next 14 days)</b></div>
  <div style="margin-top:.5rem">{rows}</div>
</div>'''
    elif earnings is None:
        earnings_html = ""   # source not wired in — render nothing
    else:
        earnings_html = (
            '<div class="alert-card">'
            '<div><b>Watchlist Earnings</b></div>'
            '<div class="muted" style="margin-top:.5rem">'
            'No watchlist earnings in the next 14 days.</div>'
            '</div>'
        )

    # ── Portfolio Greeks panel ───────────────────
    if greeks and greeks.get("open_trade_count"):
        t = greeks.get("total") or {}
        d  = t.get("delta", 0); g  = t.get("gamma", 0)
        th = t.get("theta", 0); v  = t.get("vega", 0)
        delta_cls = "pnl-pos" if d > 0 else "pnl-neg" if d < 0 else "pnl-zero"
        theta_cls = "pnl-pos" if th > 0 else "pnl-neg" if th < 0 else "pnl-zero"
        def _pos_row(p):
            warn = p.get("warning")
            warn_html = f' · ⚠ {_esc(warn)}' if warn else ''
            return (
                f'<div style="padding:.4rem 0;border-bottom:1px solid #21262d">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<b>{_esc(p.get("ticker"))} · {_esc(p.get("strategy") or "")}</b>'
                f'<span class="muted">{p.get("contracts")}c</span></div>'
                f'<div class="muted" style="font-size:.8rem;margin-top:.15rem">'
                f'Δ {p.get("delta", 0):+.1f} · Θ {p.get("theta", 0):+.1f} · '
                f'V {p.get("vega", 0):+.1f}{warn_html}'
                f'</div></div>'
            )
        rows = "".join(_pos_row(p) for p in (greeks.get("positions") or []))
        skipped_note = (
            f'<div class="muted" style="font-size:.75rem;margin-top:.4rem">'
            f'{greeks.get("skipped_legs")} leg(s) un-priced (legacy positions)</div>'
            if greeks.get("skipped_legs") else ""
        )
        greeks_html = f'''
<div class="alert-card">
  <div><b>Portfolio Greeks</b>
       <span class="muted" style="float:right">{greeks.get("open_trade_count")} open</span></div>
  <div class="grid" style="margin-top:.5rem">
    <div><span>Total Δ (delta)</span><b class="{delta_cls}">{d:+.1f}</b></div>
    <div><span>Total Θ (theta/day)</span><b class="{theta_cls}">{th:+.1f}</b></div>
    <div><span>Total V (vega)</span><b>{v:+.1f}</b></div>
    <div><span>Total Γ (gamma)</span><b>{g:+.2f}</b></div>
  </div>
  <div style="margin-top:.6rem">{rows or '<div class="muted">No priceable positions yet.</div>'}</div>
  {skipped_note}
</div>'''
    elif greeks is not None:
        greeks_html = (
            '<div class="alert-card">'
            '<div><b>Portfolio Greeks</b></div>'
            '<div class="muted" style="margin-top:.5rem">'
            'No open positions yet. Greeks aggregate once paper trades fill.</div>'
            '</div>'
        )
    else:
        greeks_html = ""

    baseline_html = _render_baseline_card()

    body = vix_html + sector_html + earnings_html + greeks_html + baseline_html
    return _render_page(
        title       = "Trading Assistant - Macro",
        heading     = "Macro Snapshot",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "macro",
    )


def _render_baseline_card() -> str:
    """
    Small footer card showing the tuned-baseline backtest numbers:
    win rate, Sharpe, source, last-run date. Reads from
    data.backtest_summary.production_stats() — that returns
    logs/backtest_summary.json when present (refreshed by
    `python -m backtests.rerun`) or static defaults otherwise.
    """
    try:
        stats = backtest_summary.production_stats() or {}
    except Exception:
        return ""
    overview = stats.get("overview") or {}
    wr   = overview.get("win_rate_pct")
    sh   = overview.get("sharpe")
    yrs  = stats.get("years") or "?"
    src  = stats.get("source") or "static_defaults"
    ver  = stats.get("version") or ""

    if wr is None and sh is None:
        return ""

    wr_s = f"{wr:.1f}%" if isinstance(wr, (int, float)) else "—"
    sh_s = f"{sh:.2f}"  if isinstance(sh, (int, float)) else "—"

    # Friendly source label
    if src.startswith("rerun_cli"):
        src_label = f"Fresh rerun — {_esc(ver)}"
    elif "backtest_summary.json" in src:
        src_label = f"Saved summary — {_esc(ver)}"
    else:
        src_label = "Static defaults (run `python -m backtests.rerun` to refresh)"

    return f'''
<div class="alert-card">
  <div class="muted" style="text-transform:uppercase;font-size:.75rem;margin-bottom:.4rem">
    Tuned baseline ({_esc(yrs)}y backtest)
  </div>
  <div class="grid">
    <div><span>Win rate</span><b>{wr_s}</b></div>
    <div><span>Sharpe</span><b>{sh_s}</b></div>
  </div>
  <div class="muted" style="font-size:.75rem;margin-top:.4rem">{src_label}</div>
</div>'''


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
# LEVELS PAGE (SPY chart + S/R + options walls + max pain)
# ─────────────────────────────────────────

_PLOTLY_CDN = '<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>'

# /levels auto-refresh: every 5 min when the tab is left open.
# Long enough to be cheap on Polygon, short enough to feel "live" for
# someone monitoring intraday. The user can disable by leaving the
# picker open — most browsers pause meta-refresh while a form has focus.
_LEVELS_AUTO_REFRESH_META = '<meta http-equiv="refresh" content="300">'


def _build_levels_figure(
    spy_df, mas: dict, swing: dict, walls: dict,
    ticker: str = "SPY",
) -> dict:
    """
    Build the Plotly figure spec (data + layout) as a plain dict.

    Layered:
      1. Candlestick for the last 90 trading days
      2. Three MA lines (20/50/200)
      3. Horizontal call walls (red dashed) + put walls (green dashed)
      4. Max pain marker (orange dotted)
      5. Recent lookback high/low (yellow dotted)

    Returns {"data": [...], "layout": {...}} — JSON-serializable.
    """
    import pandas as pd

    cols = {c.lower(): c for c in spy_df.columns} if spy_df is not None else {}
    needed = ("open", "high", "low", "close")
    if spy_df is None or any(c not in cols for c in needed):
        return {"data": [], "layout": {"title": "No SPY data available"}}

    df = spy_df.tail(90)
    x  = [str(d)[:10] for d in df.index]

    traces: list[dict] = [{
        "type":      "candlestick",
        "x":         x,
        "open":      [float(v) for v in df[cols["open"]]],
        "high":      [float(v) for v in df[cols["high"]]],
        "low":       [float(v) for v in df[cols["low"]]],
        "close":     [float(v) for v in df[cols["close"]]],
        "name":      ticker,
        "increasing": {"line": {"color": "#3fb950"}},
        "decreasing": {"line": {"color": "#f85149"}},
    }]

    closes = df[cols["close"]].rolling
    for window, color in [(20, "#58a6ff"), (50, "#bc8cff"), (200, "#f0c674")]:
        if len(df) >= window:
            ma_series = df[cols["close"]].rolling(window).mean()
            traces.append({
                "type": "scatter", "mode": "lines",
                "x":    x,
                "y":    [round(float(v), 2) if pd.notna(v) else None for v in ma_series],
                "name": f"MA{window}",
                "line": {"color": color, "width": 1.5},
            })

    shapes: list[dict] = []

    def _hline(y, color, dash, name):
        if y is None: return
        shapes.append({
            "type": "line", "xref": "paper", "x0": 0, "x1": 1,
            "y0": y, "y1": y,
            "line": {"color": color, "width": 1, "dash": dash},
        })
        # Invisible scatter so the line gets a legend entry
        traces.append({
            "type": "scatter", "mode": "lines",
            "x": [x[0], x[-1]], "y": [y, y],
            "name": name,
            "line": {"color": color, "width": 1, "dash": dash},
            "hoverinfo": "name+y",
        })

    for w in (walls.get("call_walls") or []):
        _hline(w["strike"], "#f85149", "dash",
               f'Call wall ${w["strike"]:g} ({w["open_interest"]:,} OI)')
    for w in (walls.get("put_walls") or []):
        _hline(w["strike"], "#3fb950", "dash",
               f'Put wall ${w["strike"]:g} ({w["open_interest"]:,} OI)')
    if walls.get("max_pain") is not None:
        _hline(walls["max_pain"], "#f0883e", "dot",
               f'Max pain ${walls["max_pain"]:g}')
    if swing.get("high_N") is not None:
        _hline(swing["high_N"], "#e3b341", "dot",
               f'{swing.get("lookback","?")}d high ${swing["high_N"]:g}')
    if swing.get("low_N") is not None:
        _hline(swing["low_N"], "#e3b341", "dot",
               f'{swing.get("lookback","?")}d low ${swing["low_N"]:g}')

    layout = {
        "title":       f"{ticker} — last 90 days, levels overlaid",
        "paper_bgcolor": "#0d1117",
        "plot_bgcolor":  "#0d1117",
        "font":          {"color": "#c9d1d9"},
        "xaxis": {
            "rangeslider": {"visible": False},
            "gridcolor":   "#21262d",
            "type":        "category",   # skip weekend gaps
        },
        "yaxis": {
            "gridcolor":   "#21262d",
            "title":       "Price ($)",
        },
        "margin":     {"l": 50, "r": 10, "t": 50, "b": 40},
        "shapes":     shapes,
        "showlegend": True,
        "legend":     {"orientation": "h", "y": -0.2},
        "hovermode":  "x unified",
    }
    return {"data": traces, "layout": layout}


_TICKER_RE = __import__("re").compile(r"^[A-Z][A-Z0-9.]{0,7}$")


def _normalise_ticker(raw: str | None, fallback: str = "SPY") -> str:
    """Defensive: uppercase + strict alphanumeric/dot, max 8 chars. Falls
    back to SPY for anything that doesn't match — better than letting a
    malformed value reach Polygon."""
    if not raw:
        return fallback
    candidate = raw.strip().upper()
    return candidate if _TICKER_RE.match(candidate) else fallback


def _watchlist_for_picker() -> list[str]:
    """Sorted union of all watchlist sections. Cache-only — never hits
    yfinance or Polygon. SPY is always first so the default page works
    without a populated watchlist."""
    try:
        tickers = EarningsCalendar(polygon_client=None)._load_watchlist() or []
    except Exception:
        tickers = []
    bag = {"SPY", *tickers}
    out = ["SPY"] + sorted(t for t in bag if t != "SPY")
    return out


def _render_levels(
    ticker: str, df, mas: dict, swing: dict, walls: dict
) -> str:
    """Render the /levels page body (picker + chart + side tables)."""
    import json as _json
    figure = _build_levels_figure(df, mas, swing, walls, ticker=ticker)

    # ── Ticker picker (form GET /levels/<select-value>) ──────
    options = "".join(
        f'<option value="{_esc(t)}"{" selected" if t == ticker else ""}>{_esc(t)}</option>'
        for t in _watchlist_for_picker()
    )
    picker_html = f'''
<form class="alert-card lvl-picker" method="get" action="/levels"
      onsubmit="this.action='/levels/'+this.ticker.value;return true">
  <label style="font-size:.85rem;color:#8b949e">Ticker</label>
  <select name="ticker" style="flex:1">{options}</select>
  <button type="submit">Go</button>
</form>'''

    chart_html = f'''
<div class="alert-card" style="padding:.5rem">
  <div id="lvl-chart" style="height:480px"></div>
</div>
<script>
  Plotly.newPlot(
    "lvl-chart",
    {_json.dumps(figure["data"])},
    {_json.dumps(figure["layout"])},
    {{responsive: true, displayModeBar: false}}
  );
</script>'''

    # ── Summary table ─────────────────────────────────
    close = (mas or {}).get("close")
    rows = []
    def _row(label, value, distance):
        v = "—" if value is None else f"${value:,.2f}"
        d = "—" if distance is None else f"{distance:+.2f}%"
        rows.append(
            f'<div><span>{_esc(label)}</span><b>{_esc(v)}</b>'
            f'<span class="muted" style="margin-left:.5rem">{_esc(d)}</span></div>'
        )

    if mas:
        _row("MA20",  mas.get("ma20"),  _dist(close, mas.get("ma20")))
        _row("MA50",  mas.get("ma50"),  _dist(close, mas.get("ma50")))
        _row("MA200", mas.get("ma200"), _dist(close, mas.get("ma200")))
    if swing:
        _row(f'{swing.get("lookback","?")}d high', swing.get("high_N"),
              _dist(close, swing.get("high_N")))
        _row(f'{swing.get("lookback","?")}d low',  swing.get("low_N"),
              _dist(close, swing.get("low_N")))

    summary_html = (
        f'<div class="alert-card"><div><b>Price levels</b></div>'
        f'<div class="grid" style="margin-top:.5rem">{"".join(rows)}</div></div>'
        if rows else ""
    )

    # ── Walls table ────────────────────────────────────
    walls_rows = []
    for w in (walls.get("call_walls") or []):
        walls_rows.append(
            f'<div><span>Call wall (resistance)</span>'
            f'<b style="color:#f85149">${w["strike"]:,.0f}</b>'
            f'<span class="muted" style="margin-left:.4rem">'
            f'{w["open_interest"]:,} OI · {w["distance_pct"]:+.2f}% away</span></div>'
        )
    for w in (walls.get("put_walls") or []):
        walls_rows.append(
            f'<div><span>Put wall (support)</span>'
            f'<b style="color:#3fb950">${w["strike"]:,.0f}</b>'
            f'<span class="muted" style="margin-left:.4rem">'
            f'{w["open_interest"]:,} OI · {w["distance_pct"]:+.2f}% away</span></div>'
        )
    if walls.get("max_pain") is not None:
        walls_rows.append(
            f'<div><span>Max pain</span>'
            f'<b style="color:#f0883e">${walls["max_pain"]:,.0f}</b>'
            f'<span class="muted" style="margin-left:.4rem">'
            f'{_dist(close, walls["max_pain"]):+.2f}% away</span></div>'
            if _dist(close, walls["max_pain"]) is not None else
            f'<div><span>Max pain</span>'
            f'<b style="color:#f0883e">${walls["max_pain"]:,.0f}</b></div>'
        )
    exp = walls.get("expiration")
    exp_suffix = (
        f' <span class="muted" style="margin-left:.5rem">expiry {_esc(exp)}</span>'
        if exp else ""
    )
    walls_header = f'<div><b>Heavy option strikes</b>{exp_suffix}</div>'
    walls_html = (
        f'<div class="alert-card">{walls_header}'
        f'<div class="grid" style="margin-top:.5rem">{"".join(walls_rows)}</div></div>'
        if walls_rows else
        '<div class="empty">No options chain data — chart shows price levels only. '
        'Once Polygon options access is wired this card fills in.</div>'
    )

    body = picker_html + chart_html + summary_html + walls_html
    return _render_page(
        title       = f"Trading Assistant - {ticker} Levels",
        heading     = f"{ticker} Levels",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "levels",
        extra_head  = _PLOTLY_CDN + _LEVELS_AUTO_REFRESH_META,
    )


def _dist(price, level):
    if price is None or level is None or level == 0:
        return None
    return round((price - level) / level * 100, 2)


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
    """VIX term structure + sector breadth + earnings + portfolio Greeks."""
    vix      = macro_runner.get_latest_vix()
    sector   = macro_runner.get_latest_sector()
    earnings = None
    try:
        earnings = EarningsCalendar(polygon_client=None).get_upcoming(days=14)
    except Exception:
        earnings = None
    greeks = None
    try:
        greeks = PortfolioGreeks().compute()
    except Exception:
        greeks = None
    return HTMLResponse(_render_macro(vix, sector, earnings, greeks))


@app.get("/today", response_class=HTMLResponse)
def today_page():
    """Today's morning brief: play, thesis, skip/watch conditions, macro,
    plus a small SPY sparkline that links to the full /levels/SPY view."""
    from datetime import date
    plan       = PlanLogger().get_plan(date.today().isoformat())
    spy_closes = _fetch_spy_closes_for_today(days=30) if plan else []
    return HTMLResponse(_render_today(plan, spy_closes=spy_closes))


def _build_levels_view(ticker: str) -> str:
    """Fetch the bars + levels + walls for `ticker` and return the rendered HTML."""
    from data.polygon_client    import PolygonClient
    from signals.price_levels   import recent_swing_levels, moving_average_levels
    from signals.options_walls  import load_walls

    df = None
    try:
        df = PolygonClient().get_bars(ticker, timeframe=config.SWING_PRIMARY_TIMEFRAME,
                                       limit=250, days_back=365)
    except Exception as e:
        logger.warning(f"/levels/{ticker}: bars fetch failed: {e}")

    mas   = moving_average_levels(df) if df is not None else {}
    swing = recent_swing_levels(df, lookback=50) if df is not None else {}
    walls = {}
    try:
        spot = (mas or {}).get("close")
        if spot:
            walls = load_walls(ticker, spot=spot)
    except Exception as e:
        logger.warning(f"/levels/{ticker}: walls fetch failed: {e}")

    return _render_levels(ticker, df, mas, swing, walls)


LEVELS_TICKER_COOKIE = "levels_ticker"


def _levels_response(symbol: str) -> HTMLResponse:
    """Build the response + persist the active ticker in a 90-day cookie so
    the next visit lands on the same chart."""
    body = _build_levels_view(symbol)
    resp = HTMLResponse(body)
    resp.set_cookie(
        key      = LEVELS_TICKER_COOKIE,
        value    = symbol,
        max_age  = 60 * 60 * 24 * 90,   # 90 days
        samesite = "lax",
        httponly = False,
    )
    return resp


@app.get("/levels", response_class=HTMLResponse)
def levels_page_default(
    ticker:         str | None = None,
    levels_ticker:  str | None = Cookie(default=None),
):
    """
    Picks ticker from (in order): explicit ?ticker= query → last-visited
    cookie → SPY. Picker form GETs hit this route on submit.
    """
    sym = _normalise_ticker(ticker or levels_ticker, fallback="SPY")
    return _levels_response(sym)


@app.get("/levels/{ticker}", response_class=HTMLResponse)
def levels_page_for_ticker(ticker: str):
    """Per-ticker chart + S/R levels. Ticker is validated; invalid → SPY."""
    sym = _normalise_ticker(ticker, fallback="SPY")
    return _levels_response(sym)


def _macro_chat_instance() -> MacroChat:
    """Construct MacroChat with read-only sources (no live API calls per request)."""
    # EarningsCalendar with polygon_client=None reads cache only.
    return MacroChat(earnings_calendar=EarningsCalendar(polygon_client=None))


@app.get("/chat", response_class=HTMLResponse)
def macro_chat_page():
    """Macro-aware chat: full daily context, persistent history."""
    mc = _macro_chat_instance()
    return HTMLResponse(_render_macro_chat(
        history=mc.history(),
        context_summary=mc.context_summary(),
    ))


@app.post("/chat")
def macro_chat_send(body: MacroChatRequest):
    """Send a message to the macro chat. Both turns persist to disk."""
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message required")
    reply = _macro_chat_instance().ask(msg)
    return JSONResponse({"reply": reply})


@app.post("/chat/reset")
def macro_chat_reset():
    """Clear the macro chat history."""
    MacroChat().reset_history()
    return JSONResponse({"ok": True})


@app.get("/backtest", response_class=HTMLResponse)
def backtest_page():
    """Production baseline + hypothesis history + KB + prediction accuracy."""
    return HTMLResponse(_render_backtest(
        stats      = backtest_summary.production_stats(),
        hypotheses = backtest_summary.hypotheses_by_status(),
        accuracy   = backtest_summary.prediction_accuracy(),
        kb_groups  = backtest_summary.kb_observations_by_category(),
    ))


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
