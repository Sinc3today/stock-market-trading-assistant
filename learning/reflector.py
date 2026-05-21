"""
learning/reflector.py -- Daily self-reflection via Claude.

Runs at 19:00 ET. Gathers today's prediction + resolution + plan + recent KB,
asks Claude for a structured reflection, persists:

  logs/learning/reflections/YYYY-MM-DD.md   markdown narrative
  logs/learning/knowledge.jsonl             1-3 new KB entries appended

If Claude returns malformed JSON, the raw reply is still saved to the
markdown so nothing is lost; KB simply isn't updated for that day.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.knowledge_base import KnowledgeBase, KBEntry
from learning.predictions    import PredictionLog
from journal.plan_logger     import PlanLogger
from journal.trade_recorder  import TradeRecorder
from learning.paper_broker   import AUTO_TAG


CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-5-20250929"

REFLECTOR_SYSTEM = """You are the trading assistant's self-reflection module.

Each evening you analyze today's market call against what actually happened,
plus open paper positions and recent learning, and produce TWO outputs:

  1. A short markdown narrative (5-10 sentences) for the human's evening review.
  2. 1-3 structured knowledge-base entries that capture what was *learned* today
     -- not just what happened. Each KB entry must be testable evidence that
     would change future decisions, not a generic platitude.

Be specific. Quote numbers. Connect cause to effect. If today was a skip day,
the lesson might be about why skipping was right (or wrong). If the regime was
wrong, name the missing indicator.

Return ONLY a single JSON object, no prose around it, matching this schema:

{
  "summary":   "one-sentence headline",
  "narrative": "markdown body, 5-10 sentences",
  "kb_entries": [
    {
      "category":   "regime_accuracy | gate_quality | sizing | exit_timing | market_context | edge_case",
      "claim":      "the specific lesson, <= 200 chars",
      "evidence":   "the numbers / events backing it",
      "confidence": 0.0 to 1.0,
      "tags":       ["short", "labels"]
    }
  ]
}"""


class Reflector:
    """Daily self-reflection orchestrator."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        prediction_log: PredictionLog | None = None,
        plan_logger:    PlanLogger    | None = None,
        trade_recorder: TradeRecorder | None = None,
        post_fn=None,
        api_key: str | None = None,
    ):
        self.kb       = knowledge_base or KnowledgeBase()
        self.preds    = prediction_log or PredictionLog()
        self.plans    = plan_logger    or PlanLogger()
        self.trades   = trade_recorder or TradeRecorder()
        self.post     = post_fn        # notifier.message for Pushover/Discord summary
        self.api_key  = api_key or os.getenv("ANTHROPIC_API_KEY")

        os.makedirs(os.path.join(config.LOG_DIR, "learning", "reflections"), exist_ok=True)

    # ── MAIN ──────────────────────────────────────────

    def reflect_today(self, today: date | None = None) -> dict:
        today     = today or date.today()
        today_str = today.isoformat()
        context   = self._build_context(today_str)
        prompt    = self._build_prompt(context)

        reply = self._call_claude(prompt)
        parsed, parse_err = self._parse_reply(reply)

        md_path = self._save_markdown(today_str, parsed, reply, context, parse_err)

        kb_ids: list[str] = []
        if parsed and parsed.get("kb_entries"):
            for raw in parsed["kb_entries"]:
                try:
                    entry = KBEntry(
                        date       = today_str,
                        category   = raw.get("category", "other"),
                        claim      = raw.get("claim", "")[:500],
                        evidence   = raw.get("evidence", "")[:1000],
                        confidence = float(raw.get("confidence", 0.5)),
                        source     = "reflector",
                        tags       = list(raw.get("tags") or [])[:8],
                    )
                    kb_ids.append(self.kb.append(entry))
                except Exception as e:
                    logger.warning(f"Reflector: skipping malformed KB entry: {e}")

        if self.post and parsed and parsed.get("summary"):
            try:
                self.post(
                    f"🪞 **Daily Reflection {today_str}**\n"
                    f"{parsed['summary']}\n"
                    f"_+{len(kb_ids)} KB entries -- see {md_path}_"
                )
            except Exception as e:
                logger.warning(f"Reflector: post_fn failed: {e}")

        return {
            "date":       today_str,
            "markdown":   md_path,
            "kb_ids":     kb_ids,
            "parsed":     bool(parsed),
            "parse_err":  parse_err,
        }

    # ── CONTEXT ───────────────────────────────────────

    def _build_context(self, today_str: str) -> dict:
        pred  = self.preds.get(today_str) or {}
        plan  = self.plans.get_plan(today_str) or {}
        recent_kb = self.kb.recent(days=14)
        accuracy  = self.preds.accuracy(n=30)

        open_auto = [
            t for t in self.trades.get_all_trades()
            if t.get("outcome") == "open" and AUTO_TAG in (t.get("notes_entry") or "")
        ]

        return {
            "date":            today_str,
            "prediction":      pred,
            "plan":            plan,
            "open_positions":  open_auto[-5:],   # cap for prompt size
            "recent_kb":       recent_kb[-15:],
            "rolling_accuracy": accuracy,
        }

    def _build_prompt(self, ctx: dict) -> str:
        return (
            f"DATE: {ctx['date']}\n\n"
            f"TODAY'S PREDICTION:\n{json.dumps(ctx['prediction'], indent=2)}\n\n"
            f"TODAY'S PLAN:\n{json.dumps(ctx['plan'], indent=2, default=str)}\n\n"
            f"OPEN AUTO-PAPER POSITIONS:\n{json.dumps(ctx['open_positions'], indent=2, default=str)}\n\n"
            f"ROLLING 30-DAY DIRECTIONAL ACCURACY:\n{json.dumps(ctx['rolling_accuracy'], indent=2)}\n\n"
            f"RECENT KB (last 14 days):\n{json.dumps(ctx['recent_kb'], indent=2)}\n\n"
            f"Produce the JSON reflection now."
        )

    # ── CLAUDE ────────────────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        # Anthropic first, then local Ollama fallback (keeps the learning
        # loop producing KB entries when the hosted API is capped).
        from data.llm_client import call_llm
        return call_llm(
            system          = REFLECTOR_SYSTEM,
            user            = prompt,
            anthropic_model = CLAUDE_MODEL,
            api_key         = self.api_key,
            max_tokens      = 1500,
        )

    # ── PARSING ───────────────────────────────────────

    @staticmethod
    def _parse_reply(text: str) -> tuple[dict | None, str | None]:
        if not text:
            return None, "empty reply"
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None, "no JSON object found"
        try:
            return json.loads(m.group(0)), None
        except json.JSONDecodeError as e:
            return None, f"json error: {e}"

    # ── MARKDOWN ──────────────────────────────────────

    def _save_markdown(
        self, today_str: str, parsed: dict | None, raw: str,
        ctx: dict, parse_err: str | None,
    ) -> str:
        path = os.path.join(
            config.LOG_DIR, "learning", "reflections", f"{today_str}.md"
        )
        lines = [f"# Daily Reflection -- {today_str}", ""]
        if parsed:
            lines += [
                "## Summary",
                "",
                parsed.get("summary", "_(none)_"),
                "",
                "## Narrative",
                "",
                parsed.get("narrative", "_(none)_"),
                "",
                "## KB Entries Logged",
                "",
            ]
            for e in parsed.get("kb_entries", []):
                lines += [
                    f"- **[{e.get('category')}]** {e.get('claim')} _(conf {e.get('confidence')})_",
                    f"  - evidence: {e.get('evidence')}",
                ]
            lines += [""]
        else:
            lines += [
                f"## Raw reply (parse failed: {parse_err})",
                "",
                "```",
                raw or "(empty)",
                "```",
                "",
            ]
        lines += [
            "## Context Snapshot",
            "",
            "```json",
            json.dumps({
                "prediction":        ctx.get("prediction"),
                "rolling_accuracy":  ctx.get("rolling_accuracy"),
                "open_positions_n":  len(ctx.get("open_positions", [])),
            }, indent=2, default=str),
            "```",
        ]
        with open(path, "w") as f:
            f.write("\n".join(lines))
        return path
