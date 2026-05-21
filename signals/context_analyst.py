"""
signals/context_analyst.py -- LLM "morning context analyst".

Automates the discretionary "what's going on / expected today" read the user
relies on. It synthesises the morning's context into a STRUCTURED bias the
strategy layer can consume:

    {bias, confidence, key_levels, risk_flags, summary}

Inputs (all the bot already has): today's high-impact events (event_calendar),
the pre-market gap + direction (intraday_data, pre-market bars), and recent
headlines if a news source is wired in.

Model policy (per the user's "default to local LLM" rule + cost):
    1. Run LOCAL (nucbox Ollama) first — free, private, no cap risk.
    2. ESCALATE to Anthropic only when the local read is LOW CONFIDENCE
       (e.g. ambiguous / event days) — frontier judgement where it matters.
This is a bias+confidence INPUT only; it never trades alone — it defers to
the backtested technical signals. NOT backtested (it's a live enhancement);
keep it out of the historical replay.

Timezone: the host is Central but the market is ET. All time logic here uses
US/Eastern explicitly and converts Polygon UTC bars to ET.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from loguru import logger

import config
from data import llm_client

ET = pytz.timezone("US/Eastern")

# Below this local-model confidence we escalate the read to Anthropic.
ESCALATE_BELOW_CONFIDENCE = 0.55
# Anthropic model used for escalation (matches the briefer/reflector).
ESCALATE_MODEL = "claude-sonnet-4-6"

ANALYST_SYSTEM = """You are the trading bot's morning context analyst.

You are given today's scheduled high-impact events, the SPY pre-market gap and
direction, and any recent headlines. Produce a concise, STRUCTURED read of the
day's context — NOT a trade. Your read is a bias the strategy layer weighs
against its own technical signals; when unsure, say so with low confidence.

Return ONLY a JSON object:
{
  "bias":        "bullish | bearish | neutral",
  "confidence":  0.0-1.0,
  "key_levels":  ["plain-English levels to watch, <=3"],
  "risk_flags":  ["event/risk to respect today, <=3"],
  "summary":     "<=200 chars, plain English, what's going on + expected"
}

Rules:
- If a major event (FOMC/CPI/NFP) is today, confidence should be LOW until
  after the release, and risk_flags must name it.
- Base bias on the actual pre-market reaction + events given, not guesses.
- Plain English. No jargon. Pure JSON, no preamble.
"""


class ContextAnalyst:
    """Local-first morning context read with Anthropic escalation."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.ANTHROPIC_API_KEY

    # ── MAIN ──────────────────────────────────────────

    def analyze(self, events: list[dict] | None = None,
                premarket_gap_pct: float | None = None,
                headlines: list[str] | None = None,
                today: date | None = None) -> dict:
        """
        Produce today's structured context read. Runs local first; escalates
        to Anthropic when local confidence < ESCALATE_BELOW_CONFIDENCE.
        Always returns a dict (a low-confidence neutral default if both fail).
        """
        today  = today or datetime.now(ET).date()
        prompt = self._build_prompt(events or [], premarket_gap_pct, headlines or [], today)

        raw    = llm_client.call_local(ANALYST_SYSTEM, prompt, max_tokens=600)
        parsed = self._parse(raw)
        source = "local"

        if parsed is None or parsed.get("confidence", 0.0) < ESCALATE_BELOW_CONFIDENCE:
            esc = llm_client.call_anthropic(ANALYST_SYSTEM, prompt, ESCALATE_MODEL,
                                            api_key=self.api_key, max_tokens=600)
            esc_parsed = self._parse(esc)
            if esc_parsed is not None:
                parsed, source = esc_parsed, "anthropic"
                logger.info("ContextAnalyst: escalated to Anthropic (low local confidence)")

        if parsed is None:
            logger.warning("ContextAnalyst: both backends failed/unparseable — neutral default")
            return self._default(today, source="none")

        parsed["date"]   = today.isoformat()
        parsed["source"] = source
        return parsed

    # ── PROMPT ────────────────────────────────────────

    @staticmethod
    def _build_prompt(events, gap, headlines, today) -> str:
        ev = "\n".join(f"  - {e.get('event')} ({e.get('days_away', 0)}d away)"
                       for e in events) or "  (none)"
        gap_s = f"{gap:+.2f}%" if isinstance(gap, (int, float)) else "unknown"
        hl = "\n".join(f"  - {h}" for h in headlines[:12]) or "  (none provided)"
        return (
            f"DATE: {today.isoformat()}\n\n"
            f"TODAY'S HIGH-IMPACT EVENTS:\n{ev}\n\n"
            f"SPY PRE-MARKET GAP (vs prior close): {gap_s}\n\n"
            f"RECENT HEADLINES:\n{hl}\n\n"
            f"Produce the JSON context read now."
        )

    # ── PARSING ───────────────────────────────────────

    @staticmethod
    def _parse(text: str) -> dict | None:
        if not text:
            return None
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        if "bias" not in d or "confidence" not in d:
            return None
        # Normalise.
        d["bias"] = str(d.get("bias", "neutral")).lower().strip()
        if d["bias"] not in ("bullish", "bearish", "neutral"):
            d["bias"] = "neutral"
        try:
            d["confidence"] = max(0.0, min(1.0, float(d.get("confidence", 0.0))))
        except (TypeError, ValueError):
            d["confidence"] = 0.0
        d["key_levels"] = list(d.get("key_levels") or [])[:3]
        d["risk_flags"] = list(d.get("risk_flags") or [])[:3]
        d["summary"]    = str(d.get("summary", ""))[:200]
        return d

    @staticmethod
    def _default(today: date, source: str) -> dict:
        return {
            "bias": "neutral", "confidence": 0.0, "key_levels": [],
            "risk_flags": ["context read unavailable"], "summary": "No context read.",
            "date": today.isoformat(), "source": source,
        }
