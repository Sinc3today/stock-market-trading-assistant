"""alerts/play_vision.py -- read a trade off a Robinhood screenshot via Claude vision.

Local vision (qwen2.5vl / gemma3) was too slow (~4 min) and couldn't read the
individual leg strikes — the one thing the watchdog needs. Claude reads a
structured order ticket reliably in seconds. We reuse the SAME api key + model
the AI advisor already calls, so this adds no new provider or trust boundary.

The screenshot only PRE-FILLS the manual log form; the user confirms before it's
logged, so a misread strike is caught by a human, not silently traded.

Pure helpers (build_messages / parse_reply) are unit-tested; extract() is the
thin network wrapper.
"""
from __future__ import annotations

import base64
import json
import os
import re

import requests
from loguru import logger

from alerts.ai_advisor import CLAUDE_API_URL, CLAUDE_MODEL

# Reuse the advisor's model unless overridden (e.g. COPILOT_VISION_MODEL=claude-haiku-4-5
# to trade a little accuracy for ~3x lower cost).
VISION_MODEL = os.getenv("COPILOT_VISION_MODEL", CLAUDE_MODEL)

_PROMPT = (
    "This is a screenshot of a Robinhood options order. Extract the trade as "
    "strict JSON and nothing else. Use this exact shape:\n"
    '{"ticker": "SPY", "strategy": "iron_condor|debit_spread|credit_spread|single_leg", '
    '"expiration": "YYYY-MM-DD", "net_credit_or_debit": 0.00, '
    '"max_profit": 0.00, "max_loss": 0.00, '
    '"legs": [{"action": "BUY|SELL", "type": "CALL|PUT", "strike": 0}]}\n'
    "Read every leg. strike is a number. If a field is not visible, use null. "
    "Return ONLY the JSON object."
)


def build_messages(image_b64: str, media_type: str) -> list:
    """Anthropic messages payload: the image first, then the extraction prompt."""
    return [{
        "role": "user",
        "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": _PROMPT},
        ],
    }]


def _f(v):
    """float or None — tolerant of strings, blanks, and already-None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_STRATEGY_ALIASES = {
    "iron condor": "iron_condor", "ironcondor": "iron_condor",
    "debit spread": "debit_spread", "credit spread": "credit_spread",
    "call debit spread": "debit_spread", "put debit spread": "debit_spread",
    "call credit spread": "credit_spread", "put credit spread": "credit_spread",
    "single leg": "single_leg", "call": "single_leg", "put": "single_leg",
}


def _norm_strategy(s):
    if not s:
        return None
    key = str(s).strip().lower()
    return _STRATEGY_ALIASES.get(key, key.replace(" ", "_"))


def _norm_leg(leg: dict) -> dict | None:
    action = str(leg.get("action") or "").strip().upper()
    typ = str(leg.get("option_type") or leg.get("type") or "").strip().upper()
    strike = _f(leg.get("strike"))
    if not action.startswith(("B", "S")) or strike is None or not typ:
        return None
    return {
        "action": "BUY" if action.startswith("B") else "SELL",
        "option_type": "CALL" if typ.startswith("C") else "PUT",
        "strike": strike,
        "expiry": None,   # filled from the play-level expiry below
    }


def parse_reply(text: str) -> dict:
    """Normalize Claude's JSON reply into our canonical play dict. Raises
    ValueError if no JSON object can be recovered."""
    if not text:
        raise ValueError("empty vision reply")
    # strip ``` / ```json fences, then grab the outermost {...}
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in vision reply: {text[:120]!r}")
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON in vision reply: {e}") from e

    ticker = raw.get("ticker") or raw.get("underlying") or "SPY"
    expiry = raw.get("expiration") or raw.get("expiry") or None
    entry = raw.get("net_credit_or_debit")
    if entry is None:
        entry = (raw.get("net_credit") or raw.get("net_debit")
                 or raw.get("limit_price") or raw.get("entry_price"))

    legs = []
    for leg in (raw.get("legs") or []):
        n = _norm_leg(leg)
        if n:
            n["expiry"] = expiry
            legs.append(n)

    return {
        "ticker": str(ticker).upper(),
        "strategy": _norm_strategy(raw.get("strategy")),
        "direction": (raw.get("direction") or None),
        "expiry": expiry,
        "entry_price": _f(entry),
        "max_profit": _f(raw.get("max_profit")),
        "max_loss": _f(raw.get("max_loss")),
        "legs": legs,
    }


def extract(image_bytes: bytes, media_type: str = "image/png", timeout: int = 40) -> dict:
    """Send the screenshot to Claude and return a parsed play dict. Raises
    RuntimeError (no key / API failure) or ValueError (unparseable reply)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — screenshot extract disabled")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": VISION_MODEL,
        "max_tokens": 600,
        "messages": build_messages(b64, media_type),
    }
    try:
        resp = requests.post(CLAUDE_API_URL, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    except requests.exceptions.RequestException as e:
        logger.error(f"play_vision extract failed: {e}")
        raise RuntimeError(f"vision API error: {e}") from e
    logger.info(f"play_vision: extracted {len(text)} chars via {VISION_MODEL}")
    return parse_reply(text)
