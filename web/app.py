"""
web/app.py — FastAPI Alert Detail Server

Serves a mobile-friendly alert detail page for every scanner alert.
Each alert has a unique UUID embedded in the Pushover notification URL.

Routes:
    GET  /health                   health check
    GET  /alert/{alert_id}         full detail page (HTML)
    GET  /api/alert/{alert_id}     raw alert JSON
    POST /api/chat/{alert_id}      streaming Claude Q&A (SSE)

Start standalone:
    uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload

Or it is started automatically by main.py in a background thread.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import AsyncGenerator

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ── Path resolution ───────────────────────────────────────────
# Works whether launched from main.py (CWD = project root)
# or directly with uvicorn from any directory.
PROJECT_ROOT  = Path(__file__).parent.parent
ALERT_LOG_DIR = PROJECT_ROOT / "logs" / "alerts"
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR    = Path(__file__).parent / "static"

sys.path.insert(0, str(PROJECT_ROOT))
import config

CLAUDE_MODEL = "claude-sonnet-4-6"

# ── App setup ─────────────────────────────────────────────────
app = FastAPI(title="Trading Assistant — Alert Detail", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ─────────────────────────────────────────
# REQUEST / RESPONSE MODELS
# ─────────────────────────────────────────

class ChatMessage(BaseModel):
    """A single message in the chat history."""
    role:    str   # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    """Payload for a chat turn."""
    message: str
    history: list[ChatMessage] = []


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _load_alert(alert_id: str) -> dict | None:
    """Load a stored alert JSON by its UUID."""
    path = ALERT_LOG_DIR / f"{alert_id}.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _build_system_prompt(alert: dict) -> str:
    """Build a Claude system prompt that includes the full alert context."""
    ticker    = alert.get("ticker",        "?")
    direction = alert.get("direction",     "?")
    mode      = alert.get("mode",          "?")
    score     = alert.get("final_score",    0)
    tier      = alert.get("tier",   "standard").replace("_", " ").title()
    strategy  = alert.get("strategy",      "")
    entry     = alert.get("entry",          0)
    stop      = alert.get("stop",           0)
    target    = alert.get("target",         0)
    rr        = alert.get("rr_ratio",       0)
    rsi       = alert.get("rsi",       "N/A")
    rvol      = alert.get("rvol",      "N/A")
    cvd       = alert.get("cvd_slope", "N/A")
    ma20      = alert.get("ma20",      "N/A")
    ma50      = alert.get("ma50",      "N/A")
    ma200     = alert.get("ma200",     "N/A")
    tags      = alert.get("setup_tags",    [])
    full_card = alert.get("discord_message", "")

    tags_text = "\n".join(f"  • {t}" for t in tags) if tags else "  None recorded"
    strat_label = (
        strategy.replace("_", " ").upper()
        if strategy else "Standard Swing / Intraday"
    )

    return f"""You are an expert trading coach embedded in a personal trading assistant.
The trader has opened this alert on their phone and wants to discuss it before deciding.

═══ ALERT CONTEXT ═══════════════════════════════
Ticker:     {ticker}
Direction:  {direction}
Mode:       {mode}
Strategy:   {strat_label}
Score:      {score}/100  ({tier})

Trade Levels:
  Entry:    ${entry}
  Stop:     ${stop}
  Target:   ${target}
  R/R:      {rr}:1

Indicators:
  RSI:        {rsi}
  RVOL:       {rvol}x  (vs 20-day avg)
  CVD Slope:  {cvd}
  MA20: ${ma20}   MA50: ${ma50}   MA200: ${ma200}

Setup Triggers:
{tags_text}

Full Analysis Card:
{full_card}
════════════════════════════════════════════════

Your role:
- Answer questions about THIS specific trade — always reference the actual numbers
- Help evaluate risk, timing, position sizing, and whether the edge is real
- Explain what each indicator signal means in this specific context
- If asked for invalidation levels, give specific prices based on the data above
- Be direct and concise — under 200 words unless the trader asks for detail
- If the setup looks weak or the R/R is poor, say so honestly
- Do not give generic trading advice — stay specific to this alert
- Do not tell the trader to "buy now" or make the decision for them"""


async def _stream_claude(
    alert: dict, message: str, history: list[ChatMessage]
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE chunks from Claude."""
    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        yield f"data: {json.dumps({'text': '⚠️ ANTHROPIC_API_KEY is not configured.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    client = anthropic.AsyncAnthropic(api_key=api_key)
    system = _build_system_prompt(alert)

    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": message})

    try:
        async with client.messages.stream(
            model      = CLAUDE_MODEL,
            max_tokens = 1024,
            system     = system,
            messages   = messages,
        ) as stream:
            async for text in stream.text_stream:
                yield f"data: {json.dumps({'text': text})}\n\n"
    except anthropic.AuthenticationError:
        yield f"data: {json.dumps({'text': '⚠️ Invalid ANTHROPIC_API_KEY — check your .env file.'})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'text': f'⚠️ Error: {e}'})}\n\n"

    yield "data: [DONE]\n\n"


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "model": CLAUDE_MODEL}


@app.get("/alert/{alert_id}", response_class=HTMLResponse)
async def alert_detail(request: Request, alert_id: str):
    """Serve the full alert detail page."""
    alert = _load_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Pre-compute a few display helpers for the template
    direction    = alert.get("direction", "").upper()
    alert["_dir_class"]   = "bullish" if "BULL" in direction else "bearish"
    alert["_tier_label"]  = alert.get("tier", "standard").replace("_", " ").title()
    alert["_is_spy_opts"] = bool(alert.get("strategy"))

    return templates.TemplateResponse(
        request=request,
        name="alert.html",
        context={"alert": alert, "alert_id": alert_id},
    )


@app.get("/api/alert/{alert_id}")
async def get_alert_json(alert_id: str):
    """Return the raw alert JSON (useful for debugging or future integrations)."""
    alert = _load_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@app.post("/api/chat/{alert_id}")
async def chat_endpoint(alert_id: str, body: ChatRequest):
    """Stream a Claude response about the alert via Server-Sent Events."""
    alert = _load_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    return StreamingResponse(
        _stream_claude(alert, body.message, body.history),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if proxied
        },
    )
