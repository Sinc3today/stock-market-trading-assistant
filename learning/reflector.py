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

from learning.knowledge_base    import KnowledgeBase, KBEntry
from learning.predictions       import PredictionLog
from journal.plan_logger        import PlanLogger
from journal.trade_recorder     import TradeRecorder
from learning.paper_broker      import is_auto_paper
from data.llm_client            import call_llm
from learning.anomaly_detector  import is_anomalous_day


CLAUDE_MODEL = "claude-sonnet-4-6"

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

        # Phase 4a item 5: gather anomaly facts and route to phi4 or Sonnet
        facts = self._gather_anomaly_facts(context)
        reply, route = self._call_claude(prompt, facts)
        parsed, parse_err = self._parse_reply(reply)

        # Phase 4a items 3+4: validate KB entries (Task 5 wiring — preserved)
        if parsed:
            from learning.kb_validator import validate_kb_entries
            today_numbers = self._extract_today_numbers(context)
            today_trade_ids = self._extract_today_trade_ids(context)
            today_kb_ids = self._extract_recent_kb_ids(context)
            parsed, _ = validate_kb_entries(
                parsed,
                facts={"trade_ids": today_trade_ids,
                       "today_numbers": today_numbers,
                       "kb_ids": today_kb_ids},
                default_kind="daily",
            )

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
                    f"_+{len(kb_ids)} KB entries (route: {route}) -- see {md_path}_"
                )
            except Exception as e:
                logger.warning(f"Reflector: post_fn failed: {e}")

        return {
            "date":              today_str,
            "markdown":          md_path,
            "kb_ids":            kb_ids,
            "parsed":            bool(parsed),
            "parse_err":         parse_err,
            "validator_metrics": parsed.get("_validator_metrics", {}) if parsed else {},
            "route":             route,   # Phase 4a item 5 telemetry
        }

    # ── CONTEXT ───────────────────────────────────────

    def _build_context(self, today_str: str) -> dict:
        pred  = self.preds.get(today_str) or {}
        plan  = self.plans.get_plan(today_str) or {}
        recent_kb = self.kb.recent(days=14)
        accuracy  = self.preds.accuracy(n=30, by_substrategy=True)

        open_auto = [
            t for t in self.trades.get_all_trades()
            if t.get("outcome") == "open" and is_auto_paper(t)
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

    def _call_claude(self, prompt: str, facts: dict) -> tuple[str, str]:
        """Route based on anomaly detection. Returns (reply_text, route_label).

        Normal days  → phi4 (local Ollama); call_llm escalates to Sonnet on failure.
        Anomalous days → Sonnet directly for deeper reasoning capacity.
        """
        anomalous = is_anomalous_day(facts)
        if anomalous:
            logger.info("Reflector: anomalous day → Sonnet")
            try:
                text = call_llm(
                    system               = REFLECTOR_SYSTEM,
                    user                 = prompt,
                    anthropic_model      = CLAUDE_MODEL,
                    api_key              = self.api_key,
                    max_tokens           = 1500,
                    cache_static_system  = True,
                    model_preference     = "sonnet_first",
                )
                return text, "sonnet_anomaly"
            except Exception as e:
                logger.error(f"Sonnet anomaly call failed: {e}")
                return "", "sonnet_anomaly_error"
        else:
            logger.info("Reflector: normal day → phi4")
            text = call_llm(
                system               = REFLECTOR_SYSTEM,
                user                 = prompt,
                anthropic_model      = CLAUDE_MODEL,
                api_key              = self.api_key,
                max_tokens           = 1500,
                cache_static_system  = True,
                model_preference     = "phi4_first",
            )
            # call_llm already escalates phi4→Sonnet on failure
            return text, "phi4"

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

    # ── CONTEXT HELPERS (Phase 4a) ────────────────────

    @staticmethod
    def _extract_today_numbers(ctx: dict) -> set:
        """Pull all numeric facts from today's context for evidence-check.

        Field names must match the Prediction dataclass in learning/predictions.py.
        """
        nums: set = set()
        pred = ctx.get("prediction") or {}
        # Source of truth: Prediction dataclass (learning/predictions.py lines 60-69)
        for k in ("entry_spy", "predicted_target", "predicted_stop",
                  "actual_close", "actual_move_pct", "confidence"):
            v = pred.get(k)
            if isinstance(v, (int, float)):
                nums.add(v)
        for pos in ctx.get("open_positions", []):
            for k in ("entry_price", "exit_price", "pnl_dollars", "pnl_pct"):
                v = pos.get(k)
                if isinstance(v, (int, float)):
                    nums.add(v)
        return nums

    def _extract_today_trade_ids(self, ctx: dict) -> set:
        """Pull trade_ids from today's open positions + today's closed trades.

        Today = the date string in ctx['date']. Closed-today trades are
        included so Claude can cite them as evidence without false violations.
        """
        ids = {pos.get("trade_id") for pos in ctx.get("open_positions", [])
               if pos.get("trade_id")}
        today_str = ctx.get("date", "")
        if today_str:
            for t in self.trades.get_all_trades():
                exit_date = t.get("exit_date") or ""
                if exit_date.startswith(today_str) and t.get("trade_id"):
                    ids.add(t["trade_id"])
        return ids

    @staticmethod
    def _extract_recent_kb_ids(ctx: dict) -> set:
        """Pull KB entry IDs from the recent_kb context.

        KBEntry.id is a bare 10-char lowercase hex (uuid4().hex[:10]).
        """
        return {e.get("id") for e in ctx.get("recent_kb", []) if e.get("id")}

    # ── ANOMALY DETECTION (Phase 4a item 5) ──────────

    def _gather_anomaly_facts(self, ctx: dict) -> dict:
        """Build the facts dict the anomaly detector inspects.

        Field mapping note: the Prediction dataclass (learning/predictions.py)
        has 'entry_spy', 'predicted_target', and 'actual_move_pct' — there is
        no 'predicted_move_pct' field. We derive the predicted move from
        (predicted_target - entry_spy) / entry_spy * 100 when entry_spy > 0.

        The 'regime' field DOES exist on Prediction (added at Phase 2a).

        'new_substrategies_today' tracks sub-strategies that fired for the
        first time in history. 'regime_changed_today' compares today's
        predicted regime against the prior weekday's prediction.
        """
        pred = ctx.get("prediction") or {}

        # Derive prediction miss — see predictions.py Prediction dataclass for schema.
        entry_spy        = float(pred.get("entry_spy", 0) or 0)
        predicted_target = float(pred.get("predicted_target", 0) or 0)
        actual_move_pct  = float(pred.get("actual_move_pct", 0) or 0)
        if entry_spy > 0:
            predicted_move_pct = (predicted_target - entry_spy) / entry_spy * 100
            miss_pct = actual_move_pct - predicted_move_pct
        else:
            miss_pct = 0.0

        # Stops today (from open_positions with exit_reason == "stop")
        stops = 0
        for pos in ctx.get("open_positions", []) or []:
            if pos.get("exit_reason") == "stop":
                stops += 1

        # New sub-strategies fired today: list of strategy:dte_bucket strings
        new_subs: list[str] = []
        try:
            seen_subs = self._historical_substrategies()
            for pos in ctx.get("open_positions", []) or []:
                key = f"{pos.get('strategy')}:{pos.get('dte_bucket')}"
                if key not in seen_subs:
                    new_subs.append(key)
        except Exception:
            pass

        # Regime change vs yesterday
        regime_changed = self._regime_changed_vs_yesterday(pred)

        return {
            "stops_today":              stops,
            "prediction_miss_pct":      miss_pct,
            "new_substrategies_today":  new_subs,
            "regime_changed_today":     regime_changed,
        }

    def _historical_substrategies(self) -> set[str]:
        """Set of 'strategy:dte_bucket' strings that have fired in history.

        Reads from TradeRecorder (real + simulated for fairness — a
        sub-strategy that backfilled is not 'new' in the data sense).
        """
        all_trades = self.trades.get_trades_by(include_simulated=True)
        return {
            f"{t.get('strategy')}:{t.get('dte_bucket')}"
            for t in all_trades
            if t.get("strategy") and t.get("dte_bucket")
        }

    def _regime_changed_vs_yesterday(self, today_pred: dict) -> bool:
        """Compare today's regime classification to the prior weekday's.

        The 'regime' field is populated on the Prediction dataclass (Phase 2a).
        If today's prediction has no regime key, returns False (safe default).
        """
        today_regime = today_pred.get("regime")
        if not today_regime:
            return False
        try:
            from datetime import date, timedelta
            # Walk back up to 7 days to survive long market closures (Thanksgiving, Christmas+NYD)
            for delta in range(1, 8):
                prior_str = (date.today() - timedelta(days=delta)).isoformat()
                prior = self.preds.get(prior_str)
                if prior:
                    return prior.get("regime") != today_regime
        except Exception:
            pass
        return False

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
