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

from fastapi import FastAPI, HTTPException, Cookie, Form, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
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
from learning.paper_broker import is_auto_paper

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
/* ── Left sidebar (desktop) → slide-in overlay (mobile) ───── */
.nav{grid-column:1;grid-row:1;position:sticky;top:0;height:100vh;align-self:start;
     background:var(--surface);border-right:1px solid var(--border);
     padding:1rem .8rem;display:flex;flex-direction:column;gap:.15rem;overflow-y:auto}
.nav-brand{color:var(--fg);text-decoration:none;font-weight:700;font-size:1.05rem;
           letter-spacing:-.01em;padding:.2rem .55rem .9rem;display:flex;
           align-items:center;gap:.5rem}
.nav-group{display:flex;flex-direction:column;gap:.15rem;margin-bottom:.45rem}
.nav-group-label{display:none}
.nav-ic{display:inline-flex;width:18px;height:18px;flex:0 0 auto;opacity:.85}
.nav-ic svg{width:100%;height:100%}
.nav a:not(.nav-brand){color:var(--fg-muted);text-decoration:none;font-size:.92rem;font-weight:500;
       padding:.55rem .6rem;border-radius:var(--r-sm);display:flex;align-items:center;gap:.6rem}
.nav a:not(.nav-brand):hover{color:var(--fg);background:var(--surface-2)}
.nav a.active{color:var(--accent-fg);background:var(--accent)}
.nav a.active .nav-ic{opacity:1}
.nav-spacer{flex:1;min-height:.5rem}
.theme-toggle{background:transparent;border:1px solid var(--border);color:var(--fg-muted);
              border-radius:var(--r-sm);padding:.5rem .6rem;text-align:left;cursor:pointer;
              font-weight:500;font-size:.85rem;width:100%;display:flex;
              align-items:center;gap:.6rem}
.theme-toggle:hover{color:var(--fg);border-color:var(--border-strong);filter:none}
.topbar{display:none}
.nav-scrim{display:none}
.nav-toggle-input{display:none}
.nav-toggle{display:none}
"""

_BASE_CSS = """
:root{
  --bg:#100f0d;--surface:#1a1714;--surface-2:#221e1a;--border:#2d2823;--border-strong:#3c362d;
  --fg:#ece7df;--fg-muted:#a39b8d;--fg-subtle:#6f675b;
  --accent:#1f9e8c;--accent-fg:#ffffff;--accent-weak:#0e2a25;
  --highlight:#c25a34;--highlight-weak:#2a1810;
  --data-blue:#4c8dff;--data-coral:#f3766b;
  --ok:#3fd9a8;--ok-weak:#0e2a22;--warn:#e0a83a;--warn-weak:#2a2210;
  --err:#f3766b;--err-weak:#2c1512;--info:#4c8dff;--info-weak:#11223c;
  --violet:#bc8cff;--violet-weak:#241636;
  --r-sm:6px;--r-md:9px;--r-lg:13px;--sidebar-w:236px;--content-max:1340px;
  --shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,.28);
  --font-sans:"Inter",ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
[data-theme="light"]{
  --bg:#f6f3ec;--surface:#fffdf8;--surface-2:#efe9df;--border:#e6e0d3;--border-strong:#d5cdbd;
  --fg:#241f18;--fg-muted:#5d5648;--fg-subtle:#8c8474;
  --accent:#0f9b86;--accent-fg:#ffffff;--accent-weak:#dcf3ee;
  --highlight:#b5532f;--highlight-weak:#f6e6dd;
  --data-blue:#2563eb;--data-coral:#d9534a;
  --ok:#0f9b6e;--ok-weak:#dcf2e6;--warn:#9a6700;--warn-weak:#fbf3dc;
  --err:#cf4b3e;--err-weak:#fbe7e3;--info:#0969da;--info-weak:#e7f1fd;
  --violet:#8250df;--violet-weak:#f1ebfb;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 6px 18px rgba(0,0,0,.07);
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{font-family:var(--font-sans);background:var(--bg);color:var(--fg);line-height:1.5;
     min-height:100vh;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
     overscroll-behavior-y:contain;display:grid;grid-template-columns:var(--sidebar-w) minmax(0,1fr)}
.content{grid-column:2;padding:1.6rem 2rem 4rem;max-width:var(--content-max);width:100%}
.page-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:1.25rem}
h1{font-size:1.4rem;font-weight:650;letter-spacing:-.02em;color:var(--fg)}
h2{font-size:1.05rem;font-weight:600;color:var(--fg)}
a{color:var(--accent)}
.muted{color:var(--fg-muted);font-size:.85rem}
.legs,.leg,.tnum{font-variant-numeric:tabular-nums}
.alert-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-md);
            padding:1rem 1.1rem;margin-bottom:.8rem;text-decoration:none;color:inherit;display:block;
            transition:border-color .14s ease, box-shadow .14s ease}
.alert-card:hover{border-color:var(--border-strong);box-shadow:var(--shadow)}
.alert-row{display:flex;justify-content:space-between;font-size:.9rem;margin-top:.35rem;
           flex-wrap:wrap;gap:.35rem;align-items:center}
.badge{display:inline-block;padding:.18rem .55rem;border-radius:999px;font-size:.68rem;
       background:var(--surface-2);border:1px solid var(--border);margin-right:.25rem;
       font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:var(--fg-muted)}
.empty{text-align:center;color:var(--fg-subtle);padding:2.5rem 1rem;border:1px dashed var(--border);
       border-radius:var(--r-md);background:var(--surface)}
.section{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-md);
         padding:1.1rem;margin-bottom:1rem}
.section h2{font-size:.95rem;color:var(--fg);margin-bottom:.8rem;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem 1rem;font-size:.9rem}
.grid div span{color:var(--fg-subtle);display:block;font-size:.72rem;text-transform:uppercase;
               letter-spacing:.04em;margin-bottom:.1rem}
.grid div b{font-weight:600;color:var(--fg);font-variant-numeric:tabular-nums}
.row{display:flex;gap:.5rem;flex-wrap:wrap}
.field{margin-bottom:.7rem}
label{display:block;font-size:.75rem;color:var(--fg-subtle);margin-bottom:.25rem;
      text-transform:uppercase;letter-spacing:.04em}
textarea,input,select{width:100%;background:var(--bg);border:1px solid var(--border);
       border-radius:var(--r-sm);padding:.5rem .6rem;color:var(--fg);font-family:inherit;font-size:.92rem}
textarea:focus,input:focus,select:focus{outline:2px solid var(--accent-weak);border-color:var(--accent)}
textarea{resize:vertical;min-height:60px}
button{background:var(--accent);color:var(--accent-fg);border:1px solid var(--accent);
       border-radius:var(--r-sm);padding:.55rem 1rem;font-weight:600;cursor:pointer;
       font-size:.9rem;font-family:inherit}
button:hover{filter:brightness(1.08)}
button:disabled{background:var(--surface-2);color:var(--fg-subtle);border-color:var(--border);
       cursor:not-allowed;filter:none}
.pnl-pos{color:var(--ok)}.pnl-neg{color:var(--err)}.pnl-zero{color:var(--fg-muted)}
.status-open{background:var(--info-weak);color:var(--info);border:1px solid var(--info)}
.status-win,.outcome-win{background:var(--ok-weak);color:var(--ok);border:1px solid var(--ok)}
.status-loss,.outcome-loss{background:var(--err-weak);color:var(--err);border:1px solid var(--err)}
.status-be,.outcome-be{background:var(--warn-weak);color:var(--warn);border:1px solid var(--warn)}
.status-auto{background:var(--violet-weak);color:var(--violet);border:1px solid var(--violet)}
.chat-box{height:340px;overflow-y:auto;padding:.6rem;background:var(--bg);border:1px solid var(--border);
          border-radius:var(--r-md);margin-bottom:.6rem;font-size:.9rem}
.msg{margin-bottom:.6rem;padding:.55rem .75rem;border-radius:var(--r-sm);white-space:pre-wrap;word-wrap:break-word}
.msg.user{background:var(--accent-weak);border-left:3px solid var(--accent)}
.msg.assistant{background:var(--surface-2);border-left:3px solid var(--ok)}
.entry{background:var(--bg);border:1px solid var(--border);border-radius:var(--r-sm);
       padding:.5rem .7rem;margin-top:.5rem;font-size:.85rem}
.toggle{display:flex;gap:.5rem;margin:.4rem 0}
.toggle button{flex:1;background:var(--surface-2);color:var(--fg);border:1px solid var(--border)}
.toggle button.active{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}
.lvl-picker{display:flex;gap:.5rem;align-items:center;padding:.2rem 0 .8rem}
.lvl-picker select{flex:1}
.rng-ribbon{display:flex;gap:.35rem;padding:.2rem 0 .8rem;overflow-x:auto;scrollbar-width:none}
.rng-ribbon::-webkit-scrollbar{display:none}
.rng-btn{flex:0 0 auto;padding:.4rem .75rem;border-radius:var(--r-sm);font-size:.8rem;font-weight:600;
         color:var(--fg-muted);background:var(--surface);border:1px solid var(--border);
         text-decoration:none;white-space:nowrap}
.rng-btn:hover{color:var(--fg);border-color:var(--accent)}
.rng-btn.active{color:var(--accent-fg);background:var(--accent);border-color:var(--accent)}
#ptr-indicator{position:fixed;top:0;left:0;right:0;display:flex;align-items:center;justify-content:center;
               gap:.45rem;height:46px;color:var(--fg-muted);font-size:.85rem;background:var(--bg);
               opacity:0;transform:translateY(-46px);pointer-events:none;
               transition:transform .15s ease-out, opacity .15s ease-out;z-index:9}
#ptr-indicator .ptr-spinner{font-size:1.1rem;display:inline-block}
"""

# The mobile @media block has to come AFTER _NAV_CSS so its rules win the
# cascade — earlier versions concatenated it before and the desktop
# nav-links rules (no media query) clobbered the mobile slide-down panel.
_MOBILE_CSS = """
@media (max-width:860px){
  body{grid-template-columns:1fr}
  .content{grid-column:1;padding:1rem 1rem 4rem}
  .topbar{display:flex;align-items:center;gap:.6rem;position:sticky;top:0;z-index:40;grid-column:1;
          background:var(--surface);border-bottom:1px solid var(--border);padding:.6rem .9rem}
  .topbar .nav-brand{padding:0;font-size:1rem}
  .nav-toggle{display:inline-flex;align-items:center;justify-content:center;margin-left:auto;
              background:transparent;border:1px solid var(--border);color:var(--fg);
              border-radius:var(--r-sm);padding:.35rem .6rem;font-size:1.15rem;line-height:1;cursor:pointer}
  .nav{position:fixed;left:0;top:0;bottom:0;width:250px;height:100dvh;z-index:60;
       transform:translateX(-100%);transition:transform .2s ease;box-shadow:var(--shadow)}
  .nav-toggle-input:checked ~ .nav{transform:none}
  .nav-scrim{display:block;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:55;
             opacity:0;pointer-events:none;transition:opacity .2s ease}
  .nav-toggle-input:checked ~ .nav-scrim{opacity:1;pointer-events:auto}
  h1{font-size:1.2rem}
  .grid{grid-template-columns:1fr}
  .row{flex-direction:column}
  .dash{grid-template-columns:1fr;gap:.75rem}
  /* auto (not 1/-1): span-12's `span 12` would otherwise force 11 implicit
     0-width columns whose gaps eat ~130px and squeeze the other cards */
  .span-3,.span-4,.span-5,.span-6,.span-7,.span-8,.span-12{grid-column:auto}
  #lvl-chart{height:360px !important}
}
"""

# Dashboard components — Image-1 (analytics) style: 12-col grid, stat cards with
# uppercase kicker labels, delta chips, sparklines, dotted status pills.
_DASH_CSS = """
.dash{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:1rem;margin-bottom:1.1rem}
.span-3{grid-column:span 3}.span-4{grid-column:span 4}.span-5{grid-column:span 5}
.span-6{grid-column:span 6}.span-7{grid-column:span 7}.span-8{grid-column:span 8}.span-12{grid-column:span 12}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);padding:1.1rem 1.2rem}
.kicker{font-size:.64rem;text-transform:uppercase;letter-spacing:.1em;color:var(--fg-subtle);
        font-weight:600;display:flex;align-items:center;gap:.45rem;margin-bottom:.6rem}
.kicker .dot{width:6px;height:6px;border-radius:50%;background:var(--accent);flex:0 0 auto}
.kicker .sep{color:var(--border-strong)}
.stat{display:flex;align-items:flex-start;justify-content:space-between;gap:.75rem}
.stat-value{font-size:2.05rem;font-weight:600;letter-spacing:-.02em;line-height:1.05;
            font-variant-numeric:tabular-nums;color:var(--fg)}
.stat-value .unit{font-size:.9rem;font-weight:500;color:var(--fg-muted);margin-left:.2rem}
.stat-sub{color:var(--fg-muted);font-size:.8rem;margin-top:.35rem}
.delta-chip{display:inline-flex;align-items:center;gap:.2rem;font-size:.72rem;font-weight:600;
            padding:.14rem .45rem;border-radius:999px;font-variant-numeric:tabular-nums;white-space:nowrap}
.delta-up{color:var(--ok);background:var(--ok-weak)}
.delta-down{color:var(--err);background:var(--err-weak)}
.delta-flat{color:var(--fg-muted);background:var(--surface-2)}
.spark{display:block}
.pill{display:inline-flex;align-items:center;gap:.4rem;font-size:.74rem;font-weight:600;
      padding:.2rem .55rem;border-radius:999px;background:var(--surface-2);
      border:1px solid var(--border);color:var(--fg)}
.pill .dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto}
.pill-win .dot{background:var(--ok)}.pill-loss .dot{background:var(--err)}
.pill-watch .dot{background:var(--warn)}.pill-open .dot{background:var(--accent)}
.pill-auto .dot{background:var(--violet)}
/* cards nested inside a dashboard card render as clean list rows, not boxes */
.card .alert-card{background:transparent;border:none;border-radius:0;box-shadow:none;
                  border-bottom:1px solid var(--border);padding:.85rem 0;margin:0}
.card .alert-card:hover{box-shadow:none;border-color:transparent;border-bottom-color:var(--border)}
.card .alert-card:last-of-type{border-bottom:none;padding-bottom:0}
.card .empty{background:transparent;border:none;padding:1.5rem 0}
.card .cp-note{margin:.1rem 0 .7rem}
"""

_INDEX_CSS = _BASE_CSS + _NAV_CSS + _DASH_CSS + _MOBILE_CSS

# Detail pages share the same system now (everything moved into _BASE_CSS).
_DETAIL_CSS = _INDEX_CSS


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else "—"))


# House date/time style (2026-07-10): MM-DD-YY + 12-hour at the display edge.
from alerts.fmt import fmt_date as _fdate, fmt_dt as _fdt


# Lean IA (2026-07-10 cleanup): four pages the user actually lives in.
# Retired from nav (routes stay alive for old links): alerts feed, journal,
# chats, levels, macro, chat, backtest — the Discord-era workflow.
_NAV_ICONS = {
    # small inline SVGs, stroke = currentColor (design rule: no emoji as UI icons)
    "copilot":  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15.5 8.5 13.5 13.5 8.5 15.5 10.5 10.5z"/></svg>',
    "today":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    "trades":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5-6 4 3 6-8"/><path d="M18 6h3v3"/></svg>',
    "learning": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
}
_NAV_ITEMS = [
    ("copilot",  "/copilot",  "Copilot"),
    ("today",    "/today",    "Today"),
    ("trades",   "/trades",   "Trades"),
    ("learning", "/learning", "Learning"),
]
_ICON_BELL = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>'
              '<path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>')
_ICON_MOON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>')
_BRAND_GLYPH = ('<svg viewBox="0 0 64 64" width="20" height="20" aria-hidden="true">'
                '<rect width="64" height="64" rx="14" fill="var(--accent)" opacity=".15"/>'
                '<polyline points="10,46 24,35 37,42 52,22" fill="none" stroke="var(--accent)" '
                'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
                '<circle cx="52" cy="22" r="5" fill="var(--accent)"/></svg>')


def _render_nav(active: str) -> str:
    links = []
    for key, href, text in _NAV_ITEMS:
        cls = "active" if key == active else ""
        icon = _NAV_ICONS.get(key, "")
        links.append(f'<a href="{href}" class="{cls}">'
                     f'<span class="nav-ic">{icon}</span>{text}</a>')
    groups_html = [f'<div class="nav-group">{"".join(links)}</div>']
    # Theme toggle + mobile menu auto-close. The early theme-set lives in the
    # <head> (in _render_page) to avoid a flash; this just keeps the label synced
    # and lets a nav tap close the mobile drawer.
    script = (
        '<script>'
        'function __toggleTheme(){var d=document.documentElement;'
        'var n=d.getAttribute("data-theme")==="light"?"dark":"light";'
        'd.setAttribute("data-theme",n);try{localStorage.setItem("smta-theme",n)}catch(e){}'
        '__themeLabel()}'
        'function __themeLabel(){var t=document.documentElement.getAttribute("data-theme")==="light"?"light":"dark";'
        'var el=document.getElementById("theme-label");if(el)el.textContent=t==="light"?"Light":"Dark"}'
        'document.addEventListener("DOMContentLoaded",function(){__themeLabel();'
        'var t=document.getElementById("nav-toggle");if(!t)return;'
        'document.querySelectorAll(".nav a").forEach(function(a){'
        'a.addEventListener("click",function(){t.checked=false})})});'
        '</script>'
    )
    brand = f'<a href="/copilot" class="nav-brand">{_BRAND_GLYPH}<span>SMTA</span></a>'
    return (
        '<input type="checkbox" id="nav-toggle" class="nav-toggle-input">'
        f'<header class="topbar">{brand}'
        '<label for="nav-toggle" class="nav-toggle" aria-label="Open menu">☰</label>'
        '</header>'
        '<label for="nav-toggle" class="nav-scrim" aria-hidden="true"></label>'
        '<aside class="nav">'
        f'{brand}'
        f'{"".join(groups_html)}'
        '<div class="nav-spacer"></div>'
        '<button type="button" id="push-toggle" class="theme-toggle" '
        f'style="margin-bottom:.4rem"><span class="nav-ic">{_ICON_BELL}</span>'
        '<span id="push-label">Enable notifications</span></button>'
        '<button type="button" class="theme-toggle" onclick="__toggleTheme()">'
        f'<span class="nav-ic">{_ICON_MOON}</span><span id="theme-label">Dark</span></button>'
        '</aside>'
        f'{script}'
    )


def _render_page(
    title: str, heading: str, body: str, css: str, active_nav: str,
    extra_head: str = "",
) -> str:
    """Shared HTML wrapper: sidebar shell + page content. Sidebar collapses to a
    top-bar drawer on mobile; theme (dark/light) is set pre-paint from storage."""
    theme_init = ('<script>(function(){try{if(localStorage.getItem("smta-theme")==="light")'
                  'document.documentElement.setAttribute("data-theme","light")}catch(e){}})();</script>')
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#100f0d">
<title>{html.escape(title)}</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
{theme_init}
<style>{css}</style>
{extra_head}
</head><body data-active-nav="{html.escape(active_nav)}">
{_render_nav(active_nav)}
<main class="content">
<div class="page-head"><h1>{html.escape(heading)}</h1></div>
{body}
</main>
{_PTR_INDICATOR_HTML}
{_GESTURES_SCRIPT}
{_PUSH_SCRIPT}
</body></html>"""


# PWA push: register the service worker on every load; the sidebar button
# requests permission + subscribes (VAPID) + posts the subscription. Hidden
# automatically where Web Push isn't available (plain-http origins, old iOS).
_PUSH_SCRIPT = """
<script>
(function(){
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js").catch(function(){});
  var btn = document.getElementById("push-toggle");
  if (!btn) return;
  var lbl = document.getElementById("push-label") || btn;
  if (!("PushManager" in window) || !window.isSecureContext) { btn.style.display = "none"; return; }
  function b64ToU8(s){var p="=".repeat((4-s.length%4)%4);var b=(s+p).replace(/-/g,"+").replace(/_/g,"/");
    var r=atob(b);var a=new Uint8Array(r.length);for(var i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a;}
  async function state(){
    var reg = await navigator.serviceWorker.ready;
    return await reg.pushManager.getSubscription();
  }
  async function refresh(){
    try { lbl.textContent = (await state()) ? "Notifications: on" : "Enable notifications"; }
    catch(e){}
  }
  btn.addEventListener("click", async function(){
    try {
      var reg = await navigator.serviceWorker.ready;
      var sub = await reg.pushManager.getSubscription();
      if (sub) {
        await fetch("/push/unsubscribe", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({endpoint: sub.endpoint})});
        await sub.unsubscribe();
      } else {
        var perm = await Notification.requestPermission();
        if (perm !== "granted") { lbl.textContent = "Blocked in settings"; return; }
        var key = (await (await fetch("/push/vapid-public-key")).json()).key;
        sub = await reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey: b64ToU8(key)});
        await fetch("/push/subscribe", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify(sub.toJSON())});
      }
    } catch(e) { lbl.textContent = "Push failed"; }
    refresh();
  });
  refresh();
})();
</script>
"""

# Tiny pull-to-refresh indicator that lives at the top of every page,
# hidden by default; the gesture script unhides it during a pull.
_PTR_INDICATOR_HTML = '''
<div id="ptr-indicator" aria-hidden="true">
  <span class="ptr-spinner">↻</span>
  <span class="ptr-label">Pull to refresh</span>
</div>
'''

# Gesture support: pull-to-refresh + edge-swipe-back. No JS framework,
# just touch events. Skips inputs/textareas/Plotly chart so chart pan
# isn't hijacked by the back-swipe.
_GESTURES_SCRIPT = '''
<script>
(function(){
  var PTR_THRESHOLD = 70;         // pixels of pull before refresh triggers
  var SWIPE_EDGE_X  = 30;         // pixels from left edge that count as a back-swipe start
  var SWIPE_MIN_DX  = 80;         // pixels of horizontal travel to trigger back-nav
  var HOME_NAV_KEY  = "today";    // back-swipe stops here

  var ind   = document.getElementById("ptr-indicator");
  var label = ind ? ind.querySelector(".ptr-label") : null;
  var startY = 0, startX = 0, dy = 0, dx = 0, pulling = false, swiping = false;
  var armed = false;

  function inExcludedTarget(el){
    while (el){
      if (!el.tagName) break;
      var tag = el.tagName.toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
      if (el.id === "lvl-chart") return true;
      if (el.classList && el.classList.contains("js-plotly-plot")) return true;
      el = el.parentNode;
    }
    return false;
  }

  document.addEventListener("touchstart", function(e){
    if (e.touches.length !== 1) return;
    if (inExcludedTarget(e.target)) return;
    var t = e.touches[0];
    startY = t.clientY;
    startX = t.clientX;
    dy = 0; dx = 0;
    pulling = (window.scrollY <= 0);
    swiping = (startX <= SWIPE_EDGE_X);
  }, {passive: true});

  document.addEventListener("touchmove", function(e){
    if (e.touches.length !== 1) return;
    var t = e.touches[0];
    dy = t.clientY - startY;
    dx = t.clientX - startX;

    if (pulling && dy > 0 && Math.abs(dy) > Math.abs(dx)){
      if (ind){
        ind.style.transform = "translateY(" + Math.min(dy, PTR_THRESHOLD + 20) + "px)";
        ind.style.opacity = Math.min(1, dy / PTR_THRESHOLD);
        if (dy >= PTR_THRESHOLD){
          armed = true;
          if (label) label.textContent = "Release to refresh";
        } else {
          armed = false;
          if (label) label.textContent = "Pull to refresh";
        }
      }
    }
  }, {passive: true});

  document.addEventListener("touchend", function(){
    if (pulling && armed){
      if (label) label.textContent = "Refreshing…";
      if (ind) ind.style.transform = "translateY(" + PTR_THRESHOLD + "px)";
      location.reload();
      return;
    }
    if (ind){ ind.style.transform = ""; ind.style.opacity = ""; }
    if (label) label.textContent = "Pull to refresh";
    armed = false;

    if (swiping && dx > SWIPE_MIN_DX && Math.abs(dx) > Math.abs(dy) * 2){
      var active = document.body.dataset.activeNav || "";
      // /today is the home — don't pop history past it
      if (active === HOME_NAV_KEY) return;
      if (history.length > 1) history.back();
      else                    location.href = "/today";
    }
    pulling = false; swiping = false;
  }, {passive: true});
})();
</script>
'''


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
            created   = _esc(_fdt(a.get("created_at") or ""))
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
    """Cross-trade list view with a summary stat row."""
    if not trades:
        body = '<div class="empty">No trades recorded yet.</div>'
    else:
        wins   = sum(1 for t in trades if t.get("outcome") == "win")
        losses = sum(1 for t in trades if t.get("outcome") == "loss")
        realized = sum((t.get("pnl_dollars") or 0) for t in trades
                       if isinstance(t.get("pnl_dollars"), (int, float)))
        open_n = sum(1 for t in trades if (t.get("outcome") or "open") == "open")
        total  = len(trades)
        wr     = (wins / (wins + losses) * 100) if (wins + losses) else None
        pnl_cls = "pnl-pos" if realized > 0 else "pnl-neg" if realized < 0 else "pnl-zero"
        wr_str = f"{wr:.0f}%" if wr is not None else "—"
        stat_row = (
            '<div class="dash">'
            + _stat_card('Trades <span class="sep">·</span> realized P&amp;L',
                         f'<span class="{pnl_cls}">${realized:+,.0f}</span>',
                         sub=f'{wins}W / {losses}L', span='span-3')
            + _stat_card('Trades <span class="sep">·</span> win rate', wr_str,
                         sub=f'{wins + losses} closed', span='span-3')
            + _stat_card('Trades <span class="sep">·</span> open', str(open_n),
                         sub='live + paper', span='span-3')
            + _stat_card('Trades <span class="sep">·</span> logged', str(total),
                         sub='all time', span='span-3')
            + '</div>'
        )
        rows = []
        # Newest first — trades.json is naturally append order.
        for t in reversed(trades):
            ticker    = _esc(t.get("ticker") or "?")
            direction = _esc(t.get("direction") or "")
            strategy  = _esc((t.get("strategy") or t.get("trade_type") or "").replace("_", " "))
            entry_ts  = _esc(_fdt(t.get("entry_timestamp") or t.get("entry_date") or ""))
            pnl       = t.get("pnl_dollars")
            pnl_cls   = "pnl-pos" if (pnl or 0) > 0 else "pnl-neg" if (pnl or 0) < 0 else "pnl-zero"
            pnl_str   = f"${pnl:+,.2f}" if isinstance(pnl, (int, float)) else "—"
            label, status_cls = _trade_status(t)
            # AUTO-PAPER badge for bot-generated paper trades
            auto_badge = (
                '<span class="badge status-auto">AUTO-PAPER</span>'
                if is_auto_paper(t) else ""
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
        list_card = (
            '<div class="card span-12"><div class="kicker"><span class="dot"></span>'
            f'Trade history <span class="sep">·</span> {total} logged</div>'
            f'{"".join(rows)}</div>'
        )
        body = stat_row + f'<div class="dash">{list_card}</div>'

    return _render_page(
        title       = "Trading Assistant - Trades",
        heading     = "Trade History",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "trades",
    )


# Uploaded RH screenshots land here — tailnet-only, gitignored (may hold account
# info). Pruned to ~1 day in _save_copilot_shot.
_COPILOT_UPLOAD_DIR = os.path.join(config.LOG_DIR, "copilot_uploads")

_COPILOT_CSS = """
.cp-spot{font-family:var(--font-mono,ui-monospace,monospace);color:var(--fg-muted,#52525b);margin-bottom:.75rem}
.legs{margin:.4rem 0;display:flex;flex-direction:column;gap:.2rem}
.leg{font-family:var(--font-mono,ui-monospace,monospace);font-size:.95rem;letter-spacing:.02em}
.cp-h{margin:1.4rem 0 .5rem;font-size:1rem;color:var(--fg-muted,#52525b);text-transform:uppercase;letter-spacing:.04em}
.btn-primary{appearance:none;border:1px solid var(--accent);background:var(--accent);color:var(--accent-fg);
  border-radius:6px;padding:.45rem .8rem;font-size:.9rem;font-weight:600;cursor:pointer}
.btn-primary:hover{filter:brightness(1.08)}
.btn-primary:active{transform:translateY(1px)}
.btn-ghost{display:inline-block;appearance:none;border:1px solid var(--border,#e4e4e7);background:var(--surface,#fff);
  color:var(--fg,#18181b);border-radius:6px;padding:.45rem .8rem;font-size:.9rem;font-weight:600;
  cursor:pointer;text-decoration:none}
.cp-form{background:var(--surface,#fff);border:1px solid var(--border,#e4e4e7);border-radius:8px;padding:1rem;margin-bottom:1rem}
.cp-form label{display:block;font-size:.78rem;color:var(--fg-subtle,#a1a1aa);text-transform:uppercase;
  letter-spacing:.04em;margin:.55rem 0 .2rem}
.cp-form input{width:100%;box-sizing:border-box;border:1px solid var(--border,#e4e4e7);border-radius:6px;
  padding:.5rem .6rem;font-size:1rem;font-family:var(--font-mono,ui-monospace,monospace);
  background:var(--bg,#fafafa);color:var(--fg,#18181b)}
.cp-form input:focus{outline:2px solid var(--accent-weak,#eef2ff);border-color:var(--accent,#4f46e5)}
.cp-grid{display:grid;grid-template-columns:1fr 1fr;gap:.5rem 1rem}
.cp-note{font-size:.82rem;color:var(--fg-muted,#52525b);margin:.3rem 0 .6rem}
.cp-err{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;border-radius:6px;padding:.5rem .7rem;
  font-size:.88rem;margin-bottom:.75rem}
.cp-ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d;border-radius:6px;padding:.5rem .7rem;
  font-size:.88rem;margin-bottom:.75rem}
.cp-cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem;align-items:start}
@media (max-width:760px){.cp-cols{grid-template-columns:1fr}}
.cp-shot{position:sticky;top:1rem}
.cp-shot img{width:100%;border:1px solid var(--border,#e4e4e7);border-radius:8px;display:block}
.cp-slip{display:inline-block;margin-top:.35rem;font-size:.8rem;padding:.15rem .5rem;border-radius:5px}
details.fold{border:1px solid var(--border,#e4e4e7);border-radius:8px;background:var(--surface,#fff);margin:.5rem 0}
details.fold>summary{list-style:none;cursor:pointer;padding:.6rem .75rem;display:flex;flex-wrap:wrap;
  align-items:center;gap:.45rem;font-size:.92rem;-webkit-tap-highlight-color:transparent}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::after{content:"\\203A";margin-left:auto;color:var(--fg-subtle,#a1a1aa);
  font-size:1.1rem;transform:rotate(90deg);transition:transform .15s ease}
details.fold[open]>summary::after{transform:rotate(-90deg)}
@media (prefers-reduced-motion:reduce){details.fold>summary::after{transition:none}}
details.fold>.fold-body{padding:.15rem .75rem .7rem;border-top:1px solid var(--border,#e4e4e7)}
.chips{display:flex;flex-wrap:wrap;gap:.35rem;margin:.45rem 0}
.chip{font-family:var(--font-mono,ui-monospace,monospace);font-size:.78rem;padding:.15rem .5rem;
  border:1px solid var(--border,#e4e4e7);border-radius:5px;background:var(--bg,#fafafa);color:var(--fg-muted,#52525b)}
.why-sec{margin:.6rem 0 .15rem;font-size:.72rem;color:var(--fg-subtle,#a1a1aa);
  text-transform:uppercase;letter-spacing:.05em}
.play-head{font-size:1.02rem;font-weight:600;margin:.2rem 0 .1rem}
"""


def _spy_spot():
    """Latest SPY price for stop-status, or None. Best-effort (no page-load blocking)."""
    try:
        from data.polygon_client import PolygonClient
        df = PolygonClient().get_bars("SPY", config.SWING_PRIMARY_TIMEFRAME, limit=1, days_back=3)
        return float(df["close"].iloc[-1]) if df is not None and len(df) else None
    except Exception:
        return None


def _spy_vix():
    """Current VIX for the condor calculator, or None. Best-effort."""
    try:
        from data.vix_client import VIXClient
        v = VIXClient().get_current()
        return float(v) if v else None
    except Exception:
        return None


def _render_condor_calc(spot, vix) -> str:
    """On-demand condor at the CURRENT price (0.20-delta shorts, $5 wings) — for
    when the morning notification was missed. Estimate; the 'Log this' link
    pre-fills the log form to confirm the real fill."""
    if not isinstance(spot, (int, float)):
        return '<div class="empty">SPY quote unavailable — calculator needs a live price.</div>'
    from signals.condor_calc import build_condor
    from alerts.stop_watchdog import rh_leg_lines
    try:
        c = build_condor(spot, vix)
    except Exception as e:
        logger.warning(f"condor calc failed: {e}")
        return '<div class="empty">Could not build a condor right now.</div>'
    legs = "".join(f"<div class='leg'>{_esc(l)}</div>" for l in rh_leg_lines(c["legs"]))
    vix_str = f"VIX {vix:.1f}" if isinstance(vix, (int, float)) else "VIX —"
    log_url = (f"/copilot/log?ticker=SPY&expiry={c['expiry']}"
               f"&bc={c['long_call']:g}&sc={c['short_call']:g}"
               f"&bp={c['long_put']:g}&sp={c['short_put']:g}")
    return (
        '<div class="alert-card">'
        f'<div class="muted">At SPY ${c["spot"]:,.2f} &middot; {vix_str} &middot; '
        f'~45 DTE &middot; estimate — verify vs the live chain</div>'
        f"<div class='legs'>{legs}</div>"
        '<div class="muted" style="margin-top:.25rem">'
        f'Est. credit ${c["credit"]:.2f} &middot; max profit ${c["max_profit"]:.0f} &middot; '
        f'max loss ${c["max_loss"]:.0f} &middot; breakevens '
        f'{c["breakeven_low"]:g}–{c["breakeven_high"]:g} &middot; exp {_esc(_fdate(c["expiry"]))}</div>'
        f'<a class="btn-ghost" style="margin-top:.6rem" href="{log_url}">Log this condor</a>'
        '</div>'
    )


def _render_butterfly_calc(spot, vix) -> str:
    """Low-capital alternative to the condor: long call butterfly over the same
    zone at ~half the capital (docs/STRUCTURE_COMPARISON.md). Debit = max loss —
    nothing held as collateral."""
    if not isinstance(spot, (int, float)):
        return ""
    from signals.condor_calc import build_butterfly
    try:
        b = build_butterfly(spot, vix)
    except Exception as e:
        logger.warning(f"butterfly calc failed: {e}")
        return ""
    from alerts.stop_watchdog import rh_leg_lines
    legs = "".join(f"<div class='leg'>{_esc(l)}</div>" for l in rh_leg_lines(b["legs"]))
    return (
        '<div class="alert-card">'
        f'<div class="muted">Long call butterfly {b["lower"]:g}/{b["center"]:g}/{b["upper"]:g} '
        f'&middot; ~45 DTE &middot; estimate — verify vs the live chain</div>'
        f"<div class='legs'>{legs}</div>"
        '<div class="muted" style="margin-top:.25rem">'
        f'Cost (= max loss, no collateral) <b>${b["capital"]:,.0f}</b> &middot; '
        f'max profit ${b["max_profit"]:,.0f} at pin &middot; profits '
        f'{b["breakeven_low"]:g}–{b["breakeven_high"]:g} &middot; exp {_esc(_fdate(b["expiry"]))}</div>'
        '<div class="cp-note" style="margin-top:.4rem">~half a condor\'s capital; '
        'lower win-rate &amp; ROC (see STRUCTURE_COMPARISON). Log it via screenshot '
        'after placing — the 4-slot form can\'t hold a butterfly.</div>'
        '</div>'
    )


_MTM_CACHE: dict = {}   # trade_id -> (timestamp, mtm dict | None) — live quotes are slow


def _position_mtm_cached(t: dict):
    """Best-effort live MTM dict for a position from real NBBO quotes (yfinance),
    cached ~90s. Returns None on failure. Numeric so it feeds both the per-card
    badge and the portfolio stat card without double-fetching."""
    import time
    tid = t.get("trade_id")
    now = time.time()
    hit = _MTM_CACHE.get(tid)
    if hit and now - hit[0] < 90:
        return hit[1]
    m = None
    try:
        from data.market_quotes import fetch_leg_quotes, position_mtm
        strat = (t.get("strategy") or "").lower()
        action = "debit" if ("debit" in strat or strat == "single_leg") else "credit"
        legs = fetch_leg_quotes(t.get("ticker", "SPY"), t.get("legs") or [])
        m = position_mtm(legs, entry_price=t.get("entry_price") or 0,
                         size=t.get("size") or 1, action=action)
    except Exception as e:
        logger.warning(f"copilot live MTM failed for {tid}: {e}")
    _MTM_CACHE[tid] = (now, m)
    return m


def _live_mtm_badge(t: dict) -> str:
    m = _position_mtm_cached(t)
    if not m:
        return ""
    d, sc = m["mtm_dollars"], m["spread_cost_dollars"]
    cls = "status-win" if d >= 0 else "status-loss"
    sign = "+" if d >= 0 else "−"
    return (f'<div class="cp-slip badge {cls}">live MTM {sign}${abs(d):,.0f}'
            f' · spread to close ~${sc:,.0f}</div>')


def _concentration_sub(live: list[dict]) -> str:
    """Risk-card subtitle: warn when open short strikes cluster (audit T1.2)."""
    try:
        from signals.concentration import book_concentration
        clusters = book_concentration(live, pct=getattr(config, "CONCENTRATION_GUARD_PCT", 1.5))
        if clusters:
            return (f'<span class="pnl-neg">⚠ {len(clusters)} overlapping short '
                    f'strike cluster(s)</span>')
    except Exception:
        pass
    return "max loss tied up on RH"


def _live_totals(live: list[dict]) -> dict:
    """Portfolio roll-up across live positions: summed MTM (None if no quotes),
    collateral at risk (from records, no network), and count."""
    mtm = 0.0
    have_mtm = False
    capital = 0.0
    for t in live:
        capital += float(t.get("max_loss") or 0)
        m = _position_mtm_cached(t)
        if m:
            mtm += m["mtm_dollars"]
            have_mtm = True
    return {"mtm": mtm if have_mtm else None, "capital": capital, "n": len(live)}


def _spy_recent_closes(n: int = 19) -> list:
    """Recent SPY closes from the local history CSV (fast, no network)."""
    try:
        import pandas as pd
        df = pd.read_csv(os.path.join("backtests", "spy_history.csv"))
        col = "close" if "close" in df.columns else "Close"
        return [float(x) for x in df[col].tail(n).tolist()]
    except Exception:
        return []


def _stat_card(kicker_html: str, value_html: str, sub: str = "",
               right_html: str = "", span: str = "span-4") -> str:
    sub_html = f'<div class="stat-sub">{sub}</div>' if sub else ""
    return (f'<div class="card {span}"><div class="kicker"><span class="dot"></span>{kicker_html}</div>'
            f'<div class="stat"><div><div class="stat-value">{value_html}</div>{sub_html}</div>'
            f'<div style="text-align:right">{right_html}</div></div></div>')


def _render_todays_play_card(plan: dict | None, walls: dict | None = None) -> str:
    """Today's play, right under the price row: the actionable headline + legs
    visible at a glance, with a tap-to-expand "why" panel (regime, signals,
    support/resistance) so quick decisions still teach the reasoning."""
    from alerts.stop_watchdog import rh_leg_lines
    kicker = ('<div class="kicker"><span class="dot"></span>Today\'s play '
              '<span class="sep">&middot;</span> ')
    if not plan:
        return ('<div class="card span-12">' + kicker + 'waiting</div>'
                '<div class="empty">No play yet — the 09:15 ET brief posts it here '
                'on trading days.</div></div>')

    regime_plain = _esc(_regime_label(plan.get("regime") or "?"))
    is_skip = (plan.get("action") == "SKIP") or not plan.get("strategy")
    if is_skip:
        reason = _esc(plan.get("reason") or plan.get("play") or "stand-down day")
        return ('<div class="card span-12">' + kicker + f'{regime_plain}</div>'
                f'<div class="play-head">SKIP — {reason}</div>'
                '<div class="cp-note">No entry today. The regime says stand aside; '
                'that call is part of the edge.</div>'
                '<a class="btn-ghost" href="/today">Full morning brief</a></div>')

    play = _esc(plan.get("play") or plan.get("strategy") or "—")
    conf = plan.get("confidence")
    conf_str = f' &middot; conviction {conf:.0%}' if isinstance(conf, (int, float)) else ""
    legs = rh_leg_lines(plan.get("legs") or [])
    legs_html = ("<div class='legs'>" +
                 "".join(f"<div class='leg'>{_esc(l)}</div>" for l in legs) +
                 "</div>") if legs else ""
    exp = ""
    for leg in (plan.get("legs") or []):
        e = leg.get("expiration") or leg.get("expiry")
        if e:
            exp = _fdate(str(e)[:10])
            break
    mp, ml = plan.get("max_profit"), plan.get("max_loss")
    nums = " &middot; ".join(x for x in (
        f"Exp {_esc(exp)}" if exp else "",
        f"max profit ${mp:,.0f}" if isinstance(mp, (int, float)) else "",
        f"max loss ${ml:,.0f}" if isinstance(ml, (int, float)) else "",
        _esc(plan.get("exit_rule") or ""),
    ) if x)

    # ── the expandable WHY panel ─────────────────────────────────
    m = plan.get("regime_metrics") or {}
    chips = []
    if isinstance(m.get("adx"), (int, float)):
        chips.append(f"ADX {m['adx']:g}")
    if isinstance(m.get("vix"), (int, float)):
        chips.append(f"VIX {m['vix']:g}")
    if isinstance(m.get("ivr"), (int, float)):
        chips.append(f"IVR {m['ivr']:g}")
    if isinstance(m.get("ma200_dist_%"), (int, float)):
        chips.append(f"{m['ma200_dist_%']:+.1f}% vs 200MA")
    chips_html = ('<div class="chips">' +
                  "".join(f'<span class="chip">{_esc(c)}</span>' for c in chips) +
                  '</div>') if chips else ""

    why = []
    if chips_html:
        why.append('<div class="why-sec">Regime signals</div>' + chips_html)
    if plan.get("thesis"):
        why.append(f'<div class="cp-note">{_esc(plan["thesis"])}</div>')
    fc = plan.get("forecast") or {}
    if fc.get("reasons"):
        fdir = _esc(fc.get("direction") or "")
        fconf = fc.get("confidence")
        fhead = f"Forecast: {fdir}" + (f" ({fconf:.0%})" if isinstance(fconf, (int, float)) else "")
        why.append('<div class="why-sec">' + _esc(fhead) + '</div>'
                   '<div class="chips">' +
                   "".join(f'<span class="chip">{_esc(r)}</span>' for r in fc["reasons"]) +
                   '</div>')
    w = walls or {}
    lvl = []
    for cw in (w.get("call_walls") or [])[:2]:
        lvl.append(f"resistance ${cw['strike']:g} (call wall)")
    for pw in (w.get("put_walls") or [])[:2]:
        lvl.append(f"support ${pw['strike']:g} (put wall)")
    if isinstance(w.get("max_pain"), (int, float)):
        lvl.append(f"max pain ${w['max_pain']:g}")
    if lvl:
        why.append('<div class="why-sec">Support / resistance (option walls)</div>'
                   '<div class="chips">' +
                   "".join(f'<span class="chip">{_esc(x)}</span>' for x in lvl) +
                   '</div>')
    if plan.get("plain_summary"):
        why.append('<div class="why-sec">In plain English</div>'
                   f'<div class="cp-note">{_esc(plan["plain_summary"])}</div>')
    skips = plan.get("skip_conditions") or []
    if skips:
        why.append('<div class="why-sec">I would bail if</div>'
                   '<div class="cp-note">' +
                   "<br>".join(_esc(s) for s in skips) + '</div>')
    why_html = ('<details class="fold why-fold"><summary>Why this play '
                '<span class="sep">&middot;</span> regime, signals &amp; levels</summary>'
                '<div class="fold-body">' + "".join(why) +
                '<a class="btn-ghost" style="margin-top:.6rem" href="/today">'
                'Full morning brief</a></div></details>') if why else ""

    return ('<div class="card span-12">' + kicker + f'{regime_plain}</div>'
            f'<div class="play-head">{play}</div>'
            f'<div class="muted" style="font-size:.85rem">{plan.get("ticker","SPY")}'
            f'{conf_str}</div>'
            f'{legs_html}'
            + (f'<div class="muted" style="margin-top:.25rem">{nums}</div>' if nums else "")
            + why_html + '</div>')


def _render_copilot(live: list[dict], plays: list[dict], spot, vix=None,
                    plan: dict | None = None, walls: dict | None = None) -> str:
    """Trade copilot: your live (watchdog-tracked) positions + today's plays to
    mirror on Robinhood — copy-ready RH-shaped legs + smart-stop status."""
    from alerts.stop_watchdog import rh_leg_lines, position_status
    from journal.slippage import trade_slippage
    spot_str = f"${spot:,.2f}" if isinstance(spot, (int, float)) else "—"

    def _slip(t):
        s = trade_slippage(t)
        if not s:
            return ""
        d = s["slippage_dollars"]
        mark, fill = t.get("bot_mark"), t.get("entry_price")
        if d < 0:
            txt = f"vs entry: filled {fill:g} vs bot mark {mark:g} → +${-d:,.2f} better than mark"
            cls = "status-win"
        elif d > 0:
            txt = f"vs entry: filled {fill:g} vs bot mark {mark:g} → −${d:,.2f} spread cost"
            cls = "status-loss"
        else:
            txt = f"vs entry: filled {fill:g} = bot mark {mark:g} (no slippage)"
            cls = "status-be"
        return f'<div class="cp-slip badge {cls}">{_esc(txt)}</div>'

    def _legs(t):
        lines = rh_leg_lines(t.get("legs") or [])
        return ("<div class='legs'>" +
                "".join(f"<div class='leg'>{_esc(l)}</div>" for l in lines) +
                "</div>") if lines else ""

    def _exp(t):
        for leg in (t.get("legs") or []):
            e = leg.get("expiration") or leg.get("expiry")
            if e:
                return _fdate(str(e)[:10])
        return t.get("dte_bucket") or "—"

    def _strat(t):
        return _esc((t.get("strategy") or t.get("trade_type") or "").replace("_", " "))

    def _when(t):
        return _esc(_fdt(t.get("entry_date")) if t.get("entry_date") else "")

    if live:
        cards = []
        for t in live:
            legs = t.get("legs") or []
            if isinstance(spot, (int, float)) and legs:
                label, cls = position_status(legs, spot)
            else:
                label, cls = "—", "status-open"
            # Collapsed one-liner by default (user request 2026-07-13): the
            # summary carries ticker/strategy/expiry/status; legs + MTM +
            # slippage live behind the tap.
            cards.append(f'''<details class="fold pos-fold">
  <summary><b>{_esc(t.get('ticker','SPY'))}</b> &middot; {_strat(t)}
  <span class="muted" style="font-size:.82rem">exp {_esc(_exp(t))}</span>
  <span class="badge {cls}">{label}</span></summary>
  <div class="fold-body">
  {_legs(t)}
  <div class="muted" style="margin-top:.25rem">opened {_when(t) or '—'} &middot; watchdog tracking</div>
  {_live_mtm_badge(t)}
  {_slip(t)}
  </div>
</details>''')
        live_html = "\n".join(cards)
    else:
        live_html = ('<div class="empty">No live positions logged. Mirror a play on '
                     'Robinhood, then tap "I placed it" below — the watchdog will track it.</div>')

    if plays:
        cards = []
        for t in plays:
            cards.append(f'''<div class="alert-card">
  <div><b>{_esc(t.get('ticker','SPY'))}</b> &middot; {_strat(t)}</div>
  {_legs(t)}
  <div class="muted" style="margin-top:.25rem">Exp {_esc(_exp(t))}{(' &middot; ' + _when(t)) if _when(t) else ''}</div>
  <form method="post" action="/copilot/placed" style="margin-top:.5rem">
    <input type="hidden" name="trade_id" value="{_esc(t.get('trade_id',''))}">
    <button class="btn-primary" type="submit">I placed it on RH</button>
  </form>
</div>''')
        plays_html = "\n".join(cards)
    else:
        plays_html = ('<div class="empty">No new plays today — entries land here '
                      'the moment the bot opens one (09:45–15:00 ET).</div>')

    # ── Stat row (Image-1 dashboard style) ──────────────────────────────
    from alerts.sparkline import sparkline_svg, delta_chip
    closes = _spy_recent_closes(19)
    day_delta = ""
    if isinstance(spot, (int, float)):
        prev = closes[-1] if closes else None
        closes = closes + [spot]
        if prev:
            day_delta = delta_chip(round((spot - prev) / prev * 100, 2))
    spark = sparkline_svg(closes, width=140, height=38) if len(closes) >= 2 else ""
    tot = _live_totals(live)
    spy_val = f'${spot:,.2f}' if isinstance(spot, (int, float)) else '—'
    if tot["mtm"] is None:
        mtm_val = '—'
    else:
        col = "var(--ok)" if tot["mtm"] >= 0 else "var(--err)"
        sign = "+" if tot["mtm"] >= 0 else "−"
        mtm_val = f'<span style="color:{col}">{sign}${abs(tot["mtm"]):,.0f}</span>'

    # Top row is JUST the price (user feedback 2026-07-14): MTM + collateral
    # bundle into one "Positions & risk" card at the very bottom.
    stat_row = (
        '<div class="dash">'
        + _stat_card('Market <span class="sep">·</span> SPY', spy_val,
                     sub="live underlying", right_html=spark + day_delta,
                     span="span-12")
        + '</div>'
    )
    risk_card = (
        '<div class="card span-12">'
        '<div class="kicker"><span class="dot"></span>Positions &amp; risk</div>'
        '<div class="stat"><div>'
        f'<div class="stat-value">{mtm_val}</div>'
        f'<div class="stat-sub">open MTM &middot; {tot["n"]} open position(s)</div>'
        '</div><div style="text-align:right">'
        f'<div class="stat-value">${tot["capital"]:,.0f}</div>'
        f'<div class="stat-sub">{_concentration_sub(live)}</div>'
        '</div></div></div>'
    )

    # ── Main grid: positions | condor calc, then plays + log ────────────
    positions_card = (
        '<div class="card span-7">'
        '<div class="kicker"><span class="dot"></span>Live positions <span class="sep">·</span> watchdog</div>'
        f'{live_html}</div>'
    )
    condor_card = (
        '<div class="card span-5">'
        '<div class="kicker"><span class="dot"></span>Quick calcs <span class="sep">·</span> at current price</div>'
        '<div class="cp-note">Missed the alert? Build one off the live price — mirror it, then log.</div>'
        '<details class="fold calc-fold"><summary>Iron condor @ current price</summary>'
        f'<div class="fold-body">{_render_condor_calc(spot, vix)}</div></details>'
        '<details class="fold calc-fold"><summary>Low-capital butterfly</summary>'
        f'<div class="fold-body">{_render_butterfly_calc(spot, vix)}</div></details>'
        '</div>'
    )
    plays_card = (
        '<div class="card span-12">'
        '<div class="kicker"><span class="dot"></span>Today\'s plays <span class="sep">·</span> mirror on RH</div>'
        f'{plays_html}'
        '<div style="margin-top:1rem;padding-top:1rem;border-top:1px solid var(--border)">'
        '<a class="btn-ghost" href="/copilot/log">+ Log a trade I built myself</a></div>'
        '</div>'
    )
    todays_card = _render_todays_play_card(plan, walls)
    body = stat_row + (f'<div class="dash">{todays_card}{positions_card}'
                       f'{condor_card}{plays_card}{risk_card}</div>')
    return _render_page(
        title      = "Trading Assistant - Copilot",
        heading    = "Trade Copilot",
        body       = body,
        css        = _INDEX_CSS + _COPILOT_CSS,
        active_nav = "copilot",
    )


def _render_copilot_log(prefill: dict | None = None, error: str | None = None,
                        ok: str | None = None, shot_url: str | None = None,
                        intro: str | None = None) -> str:
    """Log a live trade. Fill it three ways: (1) upload an RH screenshot — Claude
    reads it and pre-fills, with the image shown SIDE-BY-SIDE so you compare
    without app-switching; (2) confirm a bot play you placed ('I placed it');
    (3) type it. You always confirm your real credit + contracts before logging."""
    pf = prefill or {}
    if pf.get("expiry"):
        pf = {**pf, "expiry": _fdate(pf["expiry"])}   # display style in the input

    def _v(k):
        return _esc(str(pf.get(k, "")))

    msg = ""
    if error:
        msg = f'<div class="cp-err">{_esc(error)}</div>'
    elif ok:
        msg = f'<div class="cp-ok">{_esc(ok)}</div>'
    if intro:
        msg += f'<div class="cp-note">{_esc(intro)}</div>'

    upload = (
        '<form class="cp-form" method="post" action="/copilot/extract" '
        'enctype="multipart/form-data">'
        '<label>Read it off a screenshot</label>'
        '<div class="cp-note">Upload your Robinhood position/order screen — Claude '
        'reads the legs and pre-fills the form, and the screenshot stays on this '
        'page so you can check it against the fields. Tip: crop out your balance '
        'if it shows.</div>'
        '<input type="file" name="shot" accept="image/*" capture="environment">'
        '<div style="margin-top:.6rem"><button class="btn-primary" type="submit">'
        '📷 Extract from screenshot</button></div>'
        '</form>'
    )

    form = (
        '<form class="cp-form" method="post" action="/copilot/log">'
        f'<input type="hidden" name="bot_mark" value="{_v("bot_mark")}">'
        f'<label>Ticker</label><input name="ticker" value="{_v("ticker") or "SPY"}">'
        f'<label>Expiration (MM-DD-YY)</label>'
        f'<input name="expiry" value="{_v("expiry")}" placeholder="07-24-26">'
        '<label>Your actual fill — confirm these</label>'
        '<div class="cp-grid">'
        f'<div><label>Net credit / debit (per share)</label>'
        f'<input name="entry_price" value="{_v("entry_price")}" placeholder="1.55"></div>'
        f'<div><label>Contracts</label>'
        f'<input name="contracts" value="{_v("contracts")}" placeholder="2"></div>'
        '</div>'
        '<label>Strikes — leave blank what you didn\'t trade</label>'
        '<div class="cp-grid">'
        f'<div><label>Buy call</label><input name="bc" value="{_v("bc")}"></div>'
        f'<div><label>Sell call</label><input name="sc" value="{_v("sc")}"></div>'
        f'<div><label>Buy put</label><input name="bp" value="{_v("bp")}"></div>'
        f'<div><label>Sell put</label><input name="sp" value="{_v("sp")}"></div>'
        '</div>'
        '<div class="cp-grid">'
        f'<div><label>Max profit ($, optional)</label><input name="max_profit" value="{_v("max_profit")}"></div>'
        f'<div><label>Max loss ($, optional)</label><input name="max_loss" value="{_v("max_loss")}"></div>'
        '</div>'
        '<div style="margin-top:.9rem"><button class="btn-primary" type="submit">'
        'Log live trade</button> '
        '<a class="btn-ghost" href="/copilot">Cancel</a></div>'
        '</form>'
    )

    if shot_url:
        # Side-by-side: screenshot on the left, form on the right (laptop with RH
        # in one window + assistant in the other). Stacks on a phone.
        shot_panel = (f'<div class="cp-shot"><div class="cp-note">Your screenshot — '
                      f'compare against the fields</div>'
                      f'<img src="{_esc(shot_url)}" alt="uploaded trade screenshot"></div>')
        body = msg + upload + f'<div class="cp-cols">{shot_panel}<div>{form}</div></div>'
    else:
        body = msg + upload + form

    return _render_page(
        title      = "Trading Assistant - Log a trade",
        heading    = "Log a trade I built",
        body       = body,
        css        = _INDEX_CSS + _COPILOT_CSS,
        active_nav = "copilot",
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
            created = _esc(_fdt(j.get("created_at") or ""))
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
<div style="padding:.45rem 0;border-bottom:1px solid var(--border)">
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
                f'<div style="padding:.4rem 0;border-bottom:1px solid var(--border)">'
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
                f'<div style="padding:.45rem 0;border-bottom:1px solid var(--border)">'
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


def _render_learning(
    accuracy:     dict,
    skip_quality: dict,
    paper:        dict,
    predictions:  list[dict],
    kb_recent:    list[dict],
) -> str:
    """
    Live track record for the self-learning loop.

    Unlike /backtest (historical 5yr replay), this is what the bot has
    actually done in production: predictions made, outcomes scored,
    paper trades opened/closed, KB entries written.
    """
    # ── Header strip ───────────────────────────────────
    acc_n  = accuracy.get("sample") or 0
    acc_p  = accuracy.get("accuracy")
    acc_p_str = f"{acc_p:.1f}%" if isinstance(acc_p, (int, float)) and acc_n else "—"
    acc_cls = (
        "pnl-pos" if (isinstance(acc_p, (int, float)) and acc_n >= 5 and acc_p >= 60)
        else "pnl-neg" if (isinstance(acc_p, (int, float)) and acc_n >= 5 and acc_p < 40)
        else "pnl-zero"
    )
    pnl     = paper.get("total_pnl") or 0.0
    pnl_str = f"${pnl:+,.2f}" if paper.get("closed") else "—"
    pnl_cls = "pnl-pos" if pnl > 0 else "pnl-neg" if pnl < 0 else "pnl-zero"
    wr_paper      = paper.get("win_rate_pct") or 0.0
    wr_paper_str  = f"{wr_paper:.1f}%" if paper.get("closed") else "—"
    open_count    = paper.get("open") or 0
    closed_count  = paper.get("closed") or 0

    # Skip quality — was standing down the right call? Kept separate from
    # prediction accuracy so skips don't inflate the directional number.
    sk_scored = (skip_quality.get("right") or 0) + (skip_quality.get("missed") or 0)
    sk_right  = skip_quality.get("right_pct")
    sk_str = f"{sk_right:.0f}%" if isinstance(sk_right, (int, float)) and sk_scored else "—"
    sk_cls = (
        "pnl-pos" if (isinstance(sk_right, (int, float)) and sk_scored and sk_right >= 60)
        else "pnl-neg" if (isinstance(sk_right, (int, float)) and sk_scored and sk_right < 40)
        else "pnl-zero"
    )
    sk_detail = (
        f"{skip_quality.get('right', 0)} right / {skip_quality.get('missed', 0)} missed"
        if sk_scored else "no scored skips yet"
    )

    # ── Stat-card row (dashboard style): donut for accuracy, sparkline for P&L ─
    from alerts.sparkline import sparkline_svg, gauge_svg
    cum_series = paper.get("cumulative_pnl_series") or []
    gauge = (gauge_svg(acc_p) if (isinstance(acc_p, (int, float)) and acc_n) else "")
    pnl_spark = (sparkline_svg(cum_series, width=130, height=36,
                               stroke=("var(--ok)" if pnl >= 0 else "var(--err)"))
                 if len(cum_series) >= 2 else "")
    summary_html = (
        '<div class="dash">'
        + _stat_card('Predictions <span class="sep">·</span> accuracy 60d',
                     f'<span class="{acc_cls}">{acc_p_str}</span>',
                     sub=f'{acc_n} resolved', right_html=gauge, span="span-3")
        + _stat_card('Paper <span class="sep">·</span> P&amp;L',
                     f'<span class="{pnl_cls}">{pnl_str}</span>',
                     sub=f'{closed_count} closed', right_html=pnl_spark, span="span-3")
        + _stat_card('Paper <span class="sep">·</span> win rate', wr_paper_str,
                     sub=f'{open_count} open', span="span-3")
        + _stat_card('Skips <span class="sep">·</span> right call',
                     f'<span class="{sk_cls}">{sk_str}</span>', sub=sk_detail, span="span-3")
        + '</div>'
    )

    # ── Recent predictions table ───────────────────────
    if predictions:
        pred_rows = []
        for p in predictions:
            move = p.get("actual_move_pct")
            move_str = f"{move:+.2f}%" if isinstance(move, (int, float)) else "—"
            outcome = p.get("outcome")
            if outcome == "correct":
                badge = '<span class="badge status-win">✓</span>'
                move_cls = "pnl-pos"
            elif outcome == "wrong":
                badge = '<span class="badge status-loss">✗</span>'
                move_cls = "pnl-neg"
            else:
                badge = '<span class="badge status-open">pending</span>'
                move_cls = "pnl-zero"
            tradeable = p.get("tradeable")
            if tradeable is False:
                # Skip rows show whether standing down was the right call.
                verdict = p.get("skip_verdict")
                if verdict == "right":
                    badge = '<span class="badge status-win">skip ✓</span>'
                    move_cls = "pnl-pos"
                elif verdict == "missed":
                    badge = '<span class="badge status-loss">skip ✗</span>'
                    move_cls = "pnl-neg"
                elif verdict == "neutral":
                    badge = '<span class="badge status-open">skip ~</span>'
                    move_cls = "pnl-zero"
                else:
                    badge = '<span class="badge status-open">skip</span>'
                    move_cls = "pnl-zero"
            conf = p.get("confidence")
            conf_str = f"{conf:.0%}" if isinstance(conf, (int, float)) else "—"
            pred_rows.append(
                f'<div style="padding:.45rem 0;border-bottom:1px solid var(--border)">'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
                f'<span><b>{_esc(_fdate(p.get("date")))}</b> · '
                f'{_esc(p.get("regime"))} · '
                f'{_esc(p.get("direction"))} '
                f'<span class="muted" style="font-size:.75rem">({conf_str})</span></span>'
                f'<span class="{move_cls}">{move_str} {badge}</span>'
                f'</div>'
                f'</div>'
            )
        pred_html = (
            '<div class="card span-6">'
            '<div class="kicker"><span class="dot"></span>Recent predictions '
            f'<span class="sep">·</span> last {len(predictions)}</div>'
            f'{"".join(pred_rows)}'
            '</div>'
        )
    else:
        pred_html = (
            '<div class="card span-6">'
            '<div class="kicker"><span class="dot"></span>Recent predictions</div>'
            '<div class="empty">No predictions logged yet. The 09:15 ET brief writes one each weekday.</div>'
            '</div>'
        )

    # ── Open paper positions ───────────────────────────
    open_trades = paper.get("open_trades") or []
    if open_trades:
        open_rows = []
        for t in open_trades:
            open_rows.append(
                f'<div style="padding:.4rem 0;border-bottom:1px solid var(--border)">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span><b>{_esc(t.get("ticker"))}</b> · '
                f'{_esc(t.get("strategy") or t.get("option_type"))}</span>'
                f'<span class="muted">opened {_esc(_fdt(t.get("entry_date")))}</span>'
                f'</div>'
                f'<div class="muted" style="margin-top:.15rem;font-size:.8rem">'
                f'{_esc((t.get("notes_entry") or "")[:140])}</div>'
                f'</div>'
            )
        open_html = (
            '<div class="card span-6">'
            '<div class="kicker"><span class="dot"></span>Open paper positions '
            f'<span class="sep">·</span> {len(open_trades)}</div>'
            f'{"".join(open_rows)}'
            '</div>'
        )
    else:
        open_html = ""  # don't render empty section — keep page tight

    # ── Closed paper trades + cumulative P&L ───────────
    closed_trades = paper.get("closed_trades") or []
    if closed_trades:
        cum = 0.0
        closed_rows = []
        for t in closed_trades[-15:]:   # most recent 15
            pnl_t   = t.get("pnl_dollars") or 0.0
            cum    += pnl_t
            pnl_t_cls = "pnl-pos" if pnl_t > 0 else "pnl-neg" if pnl_t < 0 else "pnl-zero"
            outcome   = t.get("outcome", "—")
            badge_cls = "status-win" if outcome == "win" else "status-loss" if outcome == "loss" else "status-open"
            closed_rows.append(
                f'<div style="padding:.45rem 0;border-bottom:1px solid var(--border)">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span><b>{_esc(t.get("ticker"))}</b> · '
                f'{_esc(t.get("strategy") or t.get("option_type"))} '
                f'<span class="badge {badge_cls}" style="margin-left:.4rem">{outcome.upper()}</span></span>'
                f'<span class="{pnl_t_cls}"><b>${pnl_t:+,.2f}</b></span>'
                f'</div>'
                f'<div class="muted" style="margin-top:.15rem;font-size:.75rem">'
                f'closed {_esc(_fdt(t.get("exit_date")))} · cumulative ${cum:+,.2f}</div>'
                f'</div>'
            )
        closed_html = (
            '<div class="card span-6">'
            '<div class="kicker"><span class="dot"></span>Closed paper trades '
            f'<span class="sep">·</span> last {min(15, len(closed_trades))}</div>'
            f'{"".join(closed_rows)}'
            '</div>'
        )
    else:
        closed_html = (
            '<div class="card span-6">'
            '<div class="kicker"><span class="dot"></span>Closed paper trades</div>'
            '<div class="empty">No closed paper positions yet. They open at 09:16 ET on '
            'tradeable days and close at expiry or stop.</div>'
            '</div>'
        )

    # ── Recent KB entries (chronological, latest first) ─
    if kb_recent:
        kb_rows = []
        for e in kb_recent[:10]:
            conf = e.get("confidence")
            conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
            kb_rows.append(
                f'<div style="padding:.45rem 0;border-bottom:1px solid var(--border)">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<b>{_esc(e.get("category"))}</b>'
                f'<span class="muted" style="font-size:.75rem">{_esc(_fdate(e.get("date")))} · conf {conf_str}</span></div>'
                f'<div class="muted" style="margin-top:.15rem">{_esc((e.get("claim") or "")[:240])}</div>'
                f'</div>'
            )
        kb_html = (
            '<div class="card span-6">'
            '<div class="kicker"><span class="dot"></span>Recent KB entries '
            f'<span class="sep">·</span> last {min(10, len(kb_recent))}</div>'
            f'{"".join(kb_rows)}'
            '</div>'
        )
    else:
        kb_html = ""

    body = (summary_html
            + f'<div class="dash">{pred_html}{kb_html}{closed_html}{open_html}</div>')
    return _render_page(
        title       = "Trading Assistant - Learning",
        heading     = "Self-Learning Track Record",
        body        = body,
        css         = _INDEX_CSS,
        active_nav  = "learning",
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


def _render_spy_walls_summary(walls: dict | None, spot: float | None) -> str:
    """
    Compact wall summary card for /today: nearest call wall above price,
    nearest put wall below, max pain, all with % distance. Renders
    nothing when walls are unavailable or there's no spot to anchor.
    """
    if not walls or spot is None:
        return ""
    calls = walls.get("call_walls") or []
    puts  = walls.get("put_walls")  or []
    mp    = walls.get("max_pain")
    if not calls and not puts and mp is None:
        return ""

    # Pick nearest above-spot call wall + nearest below-spot put wall
    above = sorted([c for c in calls if c.get("strike", 0) > spot],
                   key=lambda c: c["strike"])[:1]
    below = sorted([p for p in puts  if p.get("strike", 0) < spot],
                   key=lambda p: -p["strike"])[:1]

    rows = []
    for c in above:
        rows.append(
            f'<div><span>Resistance (call wall)</span>'
            f'<b style="color:#f85149">${c["strike"]:,.0f}</b>'
            f'<span class="muted" style="margin-left:.4rem">{c["distance_pct"]:+.2f}%</span></div>'
        )
    if mp is not None:
        d = round((mp - spot) / spot * 100, 2)
        rows.append(
            f'<div><span>Max pain</span>'
            f'<b style="color:#f0883e">${mp:,.0f}</b>'
            f'<span class="muted" style="margin-left:.4rem">{d:+.2f}%</span></div>'
        )
    for p in below:
        rows.append(
            f'<div><span>Support (put wall)</span>'
            f'<b style="color:#3fb950">${p["strike"]:,.0f}</b>'
            f'<span class="muted" style="margin-left:.4rem">{p["distance_pct"]:+.2f}%</span></div>'
        )
    if not rows:
        return ""

    exp = walls.get("expiration")
    exp_str = (
        f' <span class="muted" style="font-size:.75rem">expiry {_esc(exp)}</span>'
        if exp else ""
    )
    return f'''
<a class="alert-card" href="/levels/SPY" style="text-decoration:none">
  <div style="margin-bottom:.4rem"><b>Where SPY sits vs heavy strikes</b>{exp_str}</div>
  <div class="grid">{"".join(rows)}</div>
</a>'''


def _fetch_spy_walls_for_today(spot: float | None) -> dict:
    """Best-effort SPY walls for the /today summary. Empty dict on failure."""
    if not spot:
        return {}
    try:
        from signals.options_walls import load_walls
        return load_walls("SPY", spot=float(spot), top_n=3) or {}
    except Exception as e:
        logger.warning(f"/today walls: SPY chain fetch failed: {e}")
        return {}


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


def _render_today(
    plan:       dict | None,
    spy_closes: list[float] | None = None,
    spy_walls:  dict | None        = None,
) -> str:
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
    plan_date    = _esc(_fdate(plan.get("date") or ""))

    is_skip    = (plan.get("action") == "SKIP") or not plan.get("strategy")
    regime_cls = "status-loss" if is_skip else "status-win"

    from alerts.sparkline import sparkline_svg, delta_chip
    flag = vix_ts.get("flag")
    spot = (spy_closes or [None])[-1] if spy_closes else None

    # ── Stat-card row ───────────────────────────────────
    spy_val = f'${spot:,.2f}' if isinstance(spot, (int, float)) else '—'
    spark = (sparkline_svg(spy_closes, width=140, height=38)
             if spy_closes and len(spy_closes) >= 2 else '')
    day = ''
    if spy_closes and len(spy_closes) >= 2 and spy_closes[-2]:
        day = delta_chip(round((spy_closes[-1] - spy_closes[-2]) / spy_closes[-2] * 100, 2))
    vix_label = _esc(_VIX_FLAG_LABEL.get(flag, flag)) if flag else '—'
    regime_val_cls = "pnl-neg" if is_skip else "pnl-pos"
    TV = 'font-size:1.35rem'   # text-value cards run smaller than numeric ones
    stat_row = (
        '<div class="dash">'
        + _stat_card('Market <span class="sep">·</span> SPY', spy_val,
                     sub='live underlying', right_html=spark + day, span='span-3')
        + _stat_card('Today <span class="sep">·</span> regime',
                     f'<span class="{regime_val_cls}" style="{TV}">{regime_plain}</span>',
                     sub=('standing down' if is_skip else 'tradeable'), span='span-3')
        + _stat_card('Macro <span class="sep">·</span> volatility',
                     f'<span style="{TV}">{vix_label}</span>', sub='VIX regime', span='span-3')
        + _stat_card('Play <span class="sep">·</span> structure',
                     f'<span style="{TV}">{strategy}</span>',
                     sub=f'RR {rr} &middot; {dte}d &middot; {max_p_str}/{max_l_str}', span='span-3')
        + '</div>'
    )

    # ── Featured play + market conditions ───────────────
    play_card = (
        '<div class="card span-8">'
        f'<div class="kicker"><span class="dot"></span>Today\'s play <span class="sep">·</span> {plan_date}</div>'
        f'<div style="font-size:1.15rem;margin-bottom:.7rem"><b>{play}</b> '
        f'<span class="badge {regime_cls}">{regime_plain}</span></div>'
        '<div class="grid">'
        f'<div><span>Strategy</span><b>{strategy}</b></div>'
        f'<div><span>Risk / reward</span><b>{rr}</b></div>'
        f'<div><span>Days to expiry</span><b>{dte}</b></div>'
        f'<div><span>Max win / loss</span><b>{max_p_str} / {max_l_str}</b></div>'
        '</div>'
        f'<div class="muted" style="margin-top:.7rem">Exit: {exit_rule}</div>'
        + (f'<div style="margin-top:.9rem;padding-top:.9rem;border-top:1px solid var(--border)">'
           f'<div class="kicker"><span class="dot"></span>Why this trade today</div>{summary}</div>'
           if summary else '')
        + '</div>'
    )

    # Market conditions
    macro_bits = []
    if flag:
        macro_bits.append(f'<div><span>Volatility</span><b>{_esc(_VIX_FLAG_LABEL.get(flag, flag))}</b></div>')
    signal = sector.get("signal")
    if signal:
        macro_bits.append(f'<div><span>Sector strength</span><b>{_esc(_SECTOR_SIGNAL_LABEL.get(signal, signal))}</b></div>')
    if events:
        events_str = ", ".join(f"{_esc(e.get('event'))} ({_esc(e.get('days_away'))}d)" for e in events)
        macro_bits.append(f'<div><span>Events next 48h</span><b>{events_str}</b></div>')
    macro_card = (
        '<div class="card span-4"><div class="kicker"><span class="dot"></span>Market conditions</div>'
        f'<div class="grid">{"".join(macro_bits)}</div></div>'
    ) if macro_bits else ''

    # ── Conditions + levels grid ────────────────────────
    def _condition_card(label, items, css_cls):
        if not items:
            return ""
        rows = "".join(
            f'<div style="padding:.4rem 0;border-bottom:1px solid var(--border)">• {_esc(s)}</div>'
            for s in items)
        return ('<div class="card span-4">'
                f'<div class="kicker"><span class="badge {css_cls}">{_esc(label)}</span></div>'
                f'{rows}</div>')

    skip_html  = _condition_card("Skip this trade if", skips, "status-loss")
    watch_html = _condition_card("Watch for", watches, "status-be")
    walls_inner = _render_spy_walls_summary(spy_walls or {}, spot)
    walls_card = (
        '<div class="card span-4"><div class="kicker"><span class="dot"></span>'
        f'Price vs heavy strikes</div>{walls_inner}</div>'
    ) if walls_inner else ''

    cond_grid = (f'<div class="dash">{skip_html}{watch_html}{walls_card}</div>'
                 if (skip_html or watch_html or walls_card) else '')
    body = stat_row + f'<div class="dash">{play_card}{macro_card}</div>' + cond_grid
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
        asof_str   = _esc(_fdt(vix.get("asof") or ""))

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
        s_asof      = _esc(_fdt(sector.get("asof") or ""))

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
            f'padding:.4rem 0;border-bottom:1px solid var(--border)">'
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
                f'<div style="padding:.4rem 0;border-bottom:1px solid var(--border)">'
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

    # ── Stat row ────────────────────────────────────────
    vd = vix or {}
    sd = sector or {}
    gt = (greeks or {}).get("total") or {}
    vix_now = vd.get("VIX")
    vix_now_s = f'{vix_now:.1f}' if isinstance(vix_now, (int, float)) else '—'
    vflag = vd.get("flag")
    ratio = vd.get("ratio")
    ratio_s = f'{ratio:.3f}' if isinstance(ratio, (int, float)) else '—'
    sig = sd.get("signal")
    sig_plain = _SECTOR_SIGNAL_LABEL.get(sig, sig) if sig else '—'
    theta = gt.get("theta")
    theta_s = f'{theta:+.1f}' if isinstance(theta, (int, float)) else '—'
    open_ct = (greeks or {}).get("open_trade_count") or 0
    TV = 'font-size:1.4rem'
    stat_row = (
        '<div class="dash">'
        + _stat_card('Volatility <span class="sep">·</span> VIX', vix_now_s,
                     sub=(_esc(_VIX_FLAG_LABEL.get(vflag, vflag)) if vflag else 'today'), span='span-3')
        + _stat_card('Volatility <span class="sep">·</span> term ratio', ratio_s,
                     sub='9D / 3M', span='span-3')
        + _stat_card('Breadth <span class="sep">·</span> sectors',
                     f'<span style="{TV}">{_esc(sig_plain)}</span>', sub='vs market', span='span-3')
        + _stat_card('Portfolio <span class="sep">·</span> theta/day',
                     f'<span style="{TV}">{theta_s}</span>', sub=f'{open_ct} open', span='span-3')
        + '</div>'
    )

    def _cell(html_block, span):
        return f'<div class="{span}">{html_block}</div>' if html_block else ''

    body = (
        stat_row
        + f'<div class="dash">{_cell(vix_html, "span-6")}{_cell(sector_html, "span-6")}</div>'
        + f'<div class="dash">{_cell(greeks_html, "span-8")}{_cell(earnings_html, "span-4")}</div>'
        + baseline_html
    )
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
            last_at = _esc(_fdt(c.get("last_msg_at") or ""))
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
    created = _esc(_fdt(alert.get("created_at") or ""))

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
<script>(function(){{try{{if(localStorage.getItem("smta-theme")==="light")document.documentElement.setAttribute("data-theme","light")}}catch(e){{}}}})();</script>
<style>{_DETAIL_CSS}</style>
</head><body>
{_render_nav("alerts")}
<main class="content">
<div class="page-head"><h1>Alert {html.escape(aid)}</h1></div>
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
</main>
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


# ── Chart timeframe ribbon ─────────────────────────────
# (key, label, days_back, polygon_timeframe, resample_rule, x_label_format)
# - resample_rule None = use raw bars
# - resample_rule "W-FRI" = weekly candles ending Friday
# - resample_rule "ME"    = month-end candles
_LEVELS_RANGES = [
    ("1d",  "1D",     1,    "5min", None,    "%H:%M"),
    ("7d",  "7D",    10,    "day",  None,    "%b %-d"),
    ("14d", "14D",   20,    "day",  None,    "%b %-d"),
    ("1m",  "1M",    40,    "day",  None,    "%b %-d"),
    ("3m",  "3M",   100,    "day",  None,    "%b %-d"),
    ("6m",  "6M",   200,    "day",  "W-FRI", "%b %d"),
    ("1y",  "1Y",   400,    "day",  "W-FRI", "%b %d"),
    ("5y",  "5Y",  1900,    "day",  "ME",    "%Y-%m"),
    ("all", "All", 5000,    "day",  "ME",    "%Y-%m"),
]
_DEFAULT_RANGE = "3m"
_LEVELS_RANGE_KEYS = {k for k, *_ in _LEVELS_RANGES}


def _resample_bars(df, rule: str):
    """Resample daily OHLCV bars to a coarser cadence. Used for 6M+ ranges
    so we don't render 1300 daily candles on a 5Y chart."""
    import pandas as pd
    if df is None or len(df) == 0:
        return df
    cols = {c.lower(): c for c in df.columns}
    # pandas resample needs a real DatetimeIndex
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)
    agg = {}
    if "open"   in cols: agg[cols["open"]]   = "first"
    if "high"   in cols: agg[cols["high"]]   = "max"
    if "low"    in cols: agg[cols["low"]]    = "min"
    if "close"  in cols: agg[cols["close"]]  = "last"
    if "volume" in cols: agg[cols["volume"]] = "sum"
    out = df.resample(rule).agg(agg).dropna(how="all")
    return out


def _normalise_range(key: str | None) -> str:
    if key and key.lower() in _LEVELS_RANGE_KEYS:
        return key.lower()
    return _DEFAULT_RANGE


def _range_spec(key: str) -> tuple:
    for k, label, days, tf, rule, xfmt in _LEVELS_RANGES:
        if k == key:
            return (k, label, days, tf, rule, xfmt)
    return _range_spec(_DEFAULT_RANGE)


def _build_levels_figure(
    spy_df, mas: dict, swing: dict, walls: dict,
    ticker: str = "SPY",
    range_key: str = _DEFAULT_RANGE,
) -> dict:
    """
    Build the Plotly figure spec (data + layout) as a plain dict.

    Layered:
      1. Candlestick of the requested range (intraday for 1D, daily up
         through 3M, weekly for 6M/1Y, monthly for 5Y/All).
      2. MA20 / MA50 / MA200 — computed on the FULL frame so rolling(200)
         doesn't go all-NaN on a short visible window.
      3. Horizontal call walls (red dashed) + put walls (green dashed).
      4. Max pain marker (orange dotted).
      5. Recent lookback high/low (yellow dotted).

    Returns {"data": [...], "layout": {...}} — JSON-serializable.
    """
    import pandas as pd

    cols = {c.lower(): c for c in spy_df.columns} if spy_df is not None else {}
    needed = ("open", "high", "low", "close")
    if spy_df is None or any(c not in cols for c in needed):
        return {"data": [], "layout": {"title": "No data available"}}

    # Visible candle frame: caller is responsible for trimming/resampling
    # to the right cadence (intraday/daily/weekly/monthly) before we get
    # here. MAs are computed on this same frame so they always have
    # enough data to render (when there's at least `window` bars).
    df = spy_df
    _, _, _, _, _rule, xfmt = _range_spec(range_key)
    def _fmt_idx(d):
        if hasattr(d, "strftime"):
            try:
                return d.strftime(xfmt)
            except (ValueError, TypeError) as e:   # T4#17: don't hide pipeline bugs
                logger.warning(f"levels chart: unformattable index {d!r}: {e}")
        return str(d)[:10]
    x = [_fmt_idx(d) for d in df.index]
    ma_full = {w: df[cols["close"]].rolling(w).mean() for w in (20, 50, 200)}

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
        "showlegend": False,
    }]

    for window, color in [(20, "#58a6ff"), (50, "#bc8cff"), (200, "#f0c674")]:
        series = ma_full[window]
        if not any(pd.notna(v) for v in series):
            continue   # underlying frame too short for this window
        traces.append({
            "type": "scatter", "mode": "lines",
            "x":    x,
            "y":    [round(float(v), 2) if pd.notna(v) else None for v in series],
            "name": f"MA{window}",
            "line": {"color": color, "width": 1.5},
            "hoverinfo": "name+y",
        })

    # Horizontal S/R lines via layout.shapes ONLY — no per-line scatter
    # legend entries (they used to flood the legend with 5-9 rows on a
    # phone screen). The /levels side cards already enumerate every wall.
    shapes: list[dict] = []
    def _hline(y, color, dash):
        if y is None: return
        shapes.append({
            "type": "line", "xref": "paper", "x0": 0, "x1": 1,
            "y0": y, "y1": y,
            "line": {"color": color, "width": 1, "dash": dash},
        })

    for w in (walls.get("call_walls") or []):
        _hline(w["strike"], "#f85149", "dash")
    for w in (walls.get("put_walls") or []):
        _hline(w["strike"], "#3fb950", "dash")
    if walls.get("max_pain") is not None:
        _hline(walls["max_pain"], "#f0883e", "dot")
    if swing.get("high_N") is not None:
        _hline(swing["high_N"], "#e3b341", "dot")
    if swing.get("low_N") is not None:
        _hline(swing["low_N"], "#e3b341", "dot")

    layout = {
        # Title is in the page H1 already — drop the in-chart title to free
        # ~40px of mobile real estate.
        "paper_bgcolor": "#0d1117",
        "plot_bgcolor":  "#0d1117",
        "font":          {"color": "#c9d1d9", "size": 11},
        "xaxis": {
            "rangeslider": {"visible": False},
            "gridcolor":   "#21262d",
            "type":        "category",     # skip weekend gaps
            "nticks":      6,              # ~weekly labels, not daily
            "tickangle":   0,
            "showspikes":  False,
        },
        "yaxis": {
            "gridcolor":   "#21262d",
            "title":       None,
            "tickprefix":  "$",
        },
        "margin":     {"l": 48, "r": 12, "t": 12, "b": 32},
        "shapes":     shapes,
        # Show only the 3 MA legend entries (candlestick + S/R lines opt out
        # via showlegend:False or by being shapes). Place on top to use the
        # chart edge rather than steal vertical space at the bottom.
        "showlegend": True,
        "legend":     {"orientation": "h", "y": 1.06, "x": 0,
                       "bgcolor": "rgba(0,0,0,0)", "font": {"size": 10}},
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
    ticker: str, df, mas: dict, swing: dict, walls: dict,
    range_key: str = _DEFAULT_RANGE,
) -> str:
    """Render the /levels page body (picker + range ribbon + chart + side tables)."""
    import json as _json
    figure = _build_levels_figure(df, mas, swing, walls,
                                   ticker=ticker, range_key=range_key)

    # ── Ticker picker (form GET /levels/<select-value>) ──────
    options = "".join(
        f'<option value="{_esc(t)}"{" selected" if t == ticker else ""}>{_esc(t)}</option>'
        for t in _watchlist_for_picker()
    )
    picker_html = f'''
<form class="alert-card lvl-picker" method="get" action="/levels"
      onsubmit="this.action='/levels/'+this.ticker.value+'?range={_esc(range_key)}';return true">
  <label style="font-size:.85rem;color:#8b949e">Ticker</label>
  <select name="ticker" style="flex:1">{options}</select>
  <button type="submit">Go</button>
</form>'''

    # ── Timeframe ribbon (1D / 7D / 1M / ... / All) ─────────
    range_buttons = []
    for k, label, *_ in _LEVELS_RANGES:
        active = " active" if k == range_key else ""
        href   = f"/levels/{_esc(ticker)}?range={_esc(k)}"
        range_buttons.append(
            f'<a class="rng-btn{active}" href="{href}">{_esc(label)}</a>'
        )
    range_html = (
        '<div class="alert-card rng-ribbon">'
        + "".join(range_buttons) +
        '</div>'
    )

    chart_html = f'''
<div class="alert-card" style="padding:.5rem">
  <div id="lvl-chart" style="height:480px"></div>
</div>
<script>
  Plotly.newPlot(
    "lvl-chart",
    {_json.dumps(figure["data"])},
    {_json.dumps(figure["layout"])},
    {{
      responsive: true,
      // Compact modebar: keep zoom + reset, drop the noisy stuff. The
      // user previously had no way to undo a zoom-in on the chart.
      displaylogo: false,
      modeBarButtonsToRemove: [
        "lasso2d", "select2d", "toggleSpikelines",
        "hoverClosestCartesian", "hoverCompareCartesian"
      ]
    }}
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

    body = picker_html + range_html + chart_html + summary_html + walls_html
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

# ── PWA: static assets, service worker, Web Push endpoints ──────────────────

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/static/{fname}")
def static_file(fname: str):
    fp = os.path.join(_STATIC_DIR, os.path.basename(fname))
    if not os.path.exists(fp):
        raise HTTPException(status_code=404)
    return FileResponse(fp)


@app.get("/sw.js")
def service_worker():
    # must be served from the ROOT so its scope covers the whole app
    return FileResponse(os.path.join(_STATIC_DIR, "sw.js"),
                        media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(_STATIC_DIR, "manifest.webmanifest"),
                        media_type="application/manifest+json")


@app.get("/favicon.svg")
def favicon_svg():
    return FileResponse(os.path.join(_STATIC_DIR, "favicon.svg"),
                        media_type="image/svg+xml")


@app.get("/favicon.ico")
def favicon_ico():
    return FileResponse(os.path.join(_STATIC_DIR, "favicon.ico"))


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return FileResponse(os.path.join(_STATIC_DIR, "apple-touch-icon.png"))


@app.get("/push/vapid-public-key")
def push_vapid_key():
    from alerts.webpush import vapid_keys
    return {"key": vapid_keys()["public_key"]}


@app.post("/push/subscribe")
async def push_subscribe(request: Request):
    from alerts.webpush import SubscriptionStore
    sub = await request.json()
    if not sub.get("endpoint"):
        raise HTTPException(status_code=400, detail="not a push subscription")
    added = SubscriptionStore().add(sub)
    return {"ok": True, "added": added}


@app.post("/push/unsubscribe")
async def push_unsubscribe(request: Request):
    from alerts.webpush import SubscriptionStore
    body = await request.json()
    SubscriptionStore().remove(body.get("endpoint", ""))
    return {"ok": True}


@app.get("/health")
def health():
    """Health check."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    """Home = the copilot (2026-07-10 cleanup). The old alert feed lives on at
    /alerts for old links; it's just out of the nav."""
    return RedirectResponse("/copilot", status_code=307)


@app.get("/alerts", response_class=HTMLResponse)
def alerts_feed():
    """Legacy scanner-alert feed (retired from nav, kept for old links)."""
    alerts = alert_store.get_recent_alerts(limit=20)
    return HTMLResponse(_render_index(alerts))


@app.get("/trades", response_class=HTMLResponse)
def trades_page():
    """Cross-trade history from TradeRecorder."""
    trades = TradeRecorder().get_all_trades()
    return HTMLResponse(_render_trades(trades))


def _et_today_iso() -> str:
    """Journal + plans stamp US/Eastern dates — never compare with the
    host-local date.today() (11pm-midnight CT window bug, found 07-10)."""
    import pytz
    from datetime import datetime
    return datetime.now(pytz.timezone("US/Eastern")).date().isoformat()


_WALLS_TTL_S = 600
_walls_cache: dict = {}   # ticker -> (monotonic_ts, walls)


def _copilot_walls(spot) -> dict:
    """Option walls for the why-panel, cached 10 min — the home screen must
    not pay a full chain fetch on every load."""
    import time as _time
    hit = _walls_cache.get("SPY")
    if hit and _time.monotonic() - hit[0] < _WALLS_TTL_S:
        return hit[1]
    walls = _fetch_spy_walls_for_today(spot)
    _walls_cache["SPY"] = (_time.monotonic(), walls)
    return walls


@app.get("/copilot", response_class=HTMLResponse)
def copilot_page():
    """Trade copilot — today's play under the price, live positions
    (watchdog-tracked, collapsed) + plays to mirror."""
    opens = TradeRecorder().get_open_trades()
    live  = [t for t in opens if t.get("book") == "live"]
    # Mirror section is same-day actionable ONLY — a play opened weeks ago is
    # not mirrorable at today's prices (user feedback 2026-07-14: a 06-29
    # credit spread was still shown as "today's play").
    et_today = _et_today_iso()
    plays = [t for t in opens
             if (t.get("book") or "disciplined") == "disciplined"
             and str(t.get("entry_date", "")).startswith(et_today)]
    spot  = _spy_spot()
    plan  = None
    try:
        plan = PlanLogger().get_plan(_et_today_iso())
    except Exception as e:
        logger.warning(f"/copilot plan fetch failed: {e}")
    walls = _copilot_walls(spot) if (plan and spot) else {}
    return HTMLResponse(_render_copilot(live, plays, spot, _spy_vix(),
                                        plan=plan, walls=walls))


@app.post("/copilot/placed", response_class=HTMLResponse)
def copilot_placed(trade_id: str = Form(...)):
    """'I placed it on RH' — open the log form pre-filled from the bot's play so
    you confirm your REAL fill (credit + contracts) before it's tracked. It used
    to blind-copy the bot's numbers (size 1, the bot's mark), which mismatched
    actual fills — now you enter what you really got."""
    from alerts.copilot_log import prefill_from_play
    src = next((t for t in TradeRecorder().get_open_trades()
                if t.get("trade_id") == trade_id), None)
    if not src:
        return RedirectResponse("/copilot", status_code=303)
    return HTMLResponse(_render_copilot_log(
        prefill=prefill_from_play(src),
        intro="Strikes + expiry are from the bot's play. Enter the credit and "
              "number of contracts you actually got on Robinhood, then log."))


def _save_copilot_shot(data: bytes, content_type: str) -> str:
    """Persist an uploaded screenshot locally (tailnet-only, never committed —
    may contain account info) and return its filename token. Best-effort prune of
    shots older than a day so they don't accumulate."""
    import time
    import uuid
    os.makedirs(_COPILOT_UPLOAD_DIR, exist_ok=True)
    now = time.time()
    for fn in os.listdir(_COPILOT_UPLOAD_DIR):
        fp = os.path.join(_COPILOT_UPLOAD_DIR, fn)
        try:
            if now - os.path.getmtime(fp) > 86400:
                os.unlink(fp)
        except OSError:
            pass
    ct = content_type or ""
    ext = ".png" if "png" in ct else (".jpg" if ("jpeg" in ct or "jpg" in ct) else ".img")
    name = uuid.uuid4().hex + ext
    with open(os.path.join(_COPILOT_UPLOAD_DIR, name), "wb") as f:
        f.write(data)
    return name


@app.get("/copilot/shot/{token}")
def copilot_shot(token: str):
    """Serve a previously-uploaded screenshot (tailnet-only)."""
    fp = os.path.join(_COPILOT_UPLOAD_DIR, os.path.basename(token))
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(fp)


@app.get("/copilot/log", response_class=HTMLResponse)
def copilot_log_form(ticker: str = "", expiry: str = "", entry_price: str = "",
                     contracts: str = "", bc: str = "", sc: str = "",
                     bp: str = "", sp: str = ""):
    """Manual-log form. Blank by default; pre-filled from query params when opened
    via the condor calculator's 'Log this condor' link."""
    prefill = {"ticker": ticker, "expiry": expiry, "entry_price": entry_price,
               "contracts": contracts, "bc": bc, "sc": sc, "bp": bp, "sp": sp}
    if any(prefill.values()):
        return HTMLResponse(_render_copilot_log(
            prefill=prefill,
            intro="Pre-filled from the condor calculator — confirm your real credit "
                  "+ contracts (and adjust strikes to the live chain), then log."))
    return HTMLResponse(_render_copilot_log())


@app.post("/copilot/extract", response_class=HTMLResponse)
async def copilot_extract(shot: UploadFile = File(...)):
    """Read a play off an uploaded RH screenshot (Claude vision), pre-fill the
    form, and show the screenshot SIDE-BY-SIDE so the user compares without
    app-switching. The image is always shown — even if auto-read fails — so they
    can type from it. User confirms credit + contracts before logging."""
    from alerts.play_vision import extract
    from alerts.copilot_log import prefill_from_extracted
    data = await shot.read()
    if not data:
        return HTMLResponse(_render_copilot_log(error="Empty upload — pick an image."))
    media = shot.content_type or "image/png"
    shot_url = f"/copilot/shot/{_save_copilot_shot(data, media)}"
    try:
        play = extract(data, media_type=media)
    except (RuntimeError, ValueError) as e:
        return HTMLResponse(_render_copilot_log(
            shot_url=shot_url,
            error=f"Couldn't auto-read it ({e}). Type the trade from the screenshot →"))
    prefill = prefill_from_extracted(play)
    if not any(prefill.get(k) for k in ("bc", "sc", "bp", "sp")):
        return HTMLResponse(_render_copilot_log(
            prefill=prefill, shot_url=shot_url,
            error="Couldn't read the legs — fill the strikes from the screenshot."))
    return HTMLResponse(_render_copilot_log(
        prefill=prefill, shot_url=shot_url,
        ok="Read from the screenshot — check it against the image, confirm your "
           "credit + contracts, then log."))


@app.post("/copilot/log")
def copilot_log_submit(
    ticker: str = Form("SPY"), expiry: str = Form(""),
    entry_price: str = Form(""), contracts: str = Form(""),
    max_profit: str = Form(""), max_loss: str = Form(""), bot_mark: str = Form(""),
    bc: str = Form(""), sc: str = Form(""), bp: str = Form(""), sp: str = Form(""),
):
    """Log a user-built trade so the smart-stop watchdog tracks it (book=live)."""
    from alerts.copilot_log import build_live_trade_kwargs
    form = {"ticker": ticker, "expiry": expiry, "entry_price": entry_price,
            "contracts": contracts, "max_profit": max_profit, "max_loss": max_loss,
            "bot_mark": bot_mark,
            "bc": bc, "sc": sc, "bp": bp, "sp": sp}
    try:
        kwargs = build_live_trade_kwargs(form)
    except ValueError as e:
        return HTMLResponse(_render_copilot_log(prefill=form, error=str(e)))
    TradeRecorder().log_entry(**kwargs)
    return RedirectResponse("/copilot", status_code=303)


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
    """Today's morning brief: play, thesis, skip/watch, macro, plus a
    SPY sparkline and a "where price sits vs heavy strikes" summary."""
    plan       = PlanLogger().get_plan(_et_today_iso())
    spy_closes = _fetch_spy_closes_for_today(days=30) if plan else []
    spot       = spy_closes[-1] if spy_closes else None
    spy_walls  = _fetch_spy_walls_for_today(spot)   if spot     else {}
    return HTMLResponse(_render_today(
        plan, spy_closes=spy_closes, spy_walls=spy_walls,
    ))


def _build_levels_view(ticker: str, range_key: str = _DEFAULT_RANGE) -> str:
    """Fetch the bars + levels + walls for `ticker` at the requested range
    and return the rendered HTML. Range determines:
      - Polygon timeframe (5min vs day)
      - days_back / limit
      - whether to resample (weekly for 6M+ daily, monthly for 5Y+)
    """
    from data.polygon_client    import PolygonClient
    from signals.price_levels   import recent_swing_levels, moving_average_levels
    from signals.options_walls  import load_walls

    range_key = _normalise_range(range_key)
    _, _, days_back, polygon_tf, resample_rule, _ = _range_spec(range_key)

    df = None
    try:
        # Limit needs headroom over the bar-count estimate: ~78 5-min bars
        # per trading day, ~22 trading days per month, etc.
        limit = max(500, days_back * (78 if polygon_tf == "5min" else 2))
        df = PolygonClient().get_bars(
            ticker, timeframe=polygon_tf, limit=limit, days_back=days_back + 30,
        )
    except Exception as e:
        logger.warning(f"/levels/{ticker} ({range_key}): bars fetch failed: {e}")

    if df is not None and resample_rule and len(df) > 0:
        try:
            df = _resample_bars(df, resample_rule)
        except Exception as e:
            logger.warning(f"/levels/{ticker} ({range_key}): resample failed: {e}")

    mas   = moving_average_levels(df) if df is not None else {}
    swing = recent_swing_levels(df, lookback=min(50, len(df) if df is not None else 0)) \
            if df is not None else {}
    walls = {}
    try:
        spot = (mas or {}).get("close")
        if spot:
            walls = load_walls(ticker, spot=spot)
    except Exception as e:
        logger.warning(f"/levels/{ticker} ({range_key}): walls fetch failed: {e}")

    return _render_levels(ticker, df, mas, swing, walls, range_key=range_key)


LEVELS_TICKER_COOKIE = "levels_ticker"
LEVELS_RANGE_COOKIE  = "levels_range"


def _levels_response(symbol: str, range_key: str) -> HTMLResponse:
    """Build the response + persist the active ticker and range in
    90-day cookies so the next visit lands on the same chart at the
    same timeframe."""
    body = _build_levels_view(symbol, range_key=range_key)
    resp = HTMLResponse(body)
    resp.set_cookie(LEVELS_TICKER_COOKIE, symbol,    max_age=60*60*24*90,
                    samesite="lax", httponly=False)
    resp.set_cookie(LEVELS_RANGE_COOKIE,  range_key, max_age=60*60*24*90,
                    samesite="lax", httponly=False)
    return resp


@app.get("/levels", response_class=HTMLResponse)
def levels_page_default(
    ticker:         str | None = None,
    range:          str | None = None,
    levels_ticker:  str | None = Cookie(default=None),
    levels_range:   str | None = Cookie(default=None),
):
    """
    Picks ticker from (in order): explicit ?ticker= query → last-visited
    cookie → SPY. Range from ?range= → cookie → default.
    """
    sym  = _normalise_ticker(ticker or levels_ticker, fallback="SPY")
    rng  = _normalise_range(range  or levels_range)
    return _levels_response(sym, rng)


@app.get("/levels/{ticker}", response_class=HTMLResponse)
def levels_page_for_ticker(
    ticker: str,
    range:  str | None = None,
    levels_range: str | None = Cookie(default=None),
):
    """Per-ticker chart + S/R levels. Ticker is validated; invalid → SPY.
    Range from ?range= → cookie → default."""
    sym = _normalise_ticker(ticker, fallback="SPY")
    rng = _normalise_range(range or levels_range)
    return _levels_response(sym, rng)


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


@app.get("/learning", response_class=HTMLResponse)
def learning_page():
    """Live track record: predictions, paper P&L, KB growth — the bot's report card."""
    from learning.knowledge_base import KnowledgeBase
    return HTMLResponse(_render_learning(
        accuracy     = backtest_summary.prediction_accuracy(),
        skip_quality = backtest_summary.skip_quality(),
        paper        = backtest_summary.paper_trade_stats(),
        predictions  = backtest_summary.recent_predictions(n=14),
        kb_recent    = KnowledgeBase().recent(days=30),
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
