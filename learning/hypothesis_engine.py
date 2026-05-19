"""
learning/hypothesis_engine.py -- Weekly hypothesis proposal.

Runs Saturday 10:00 ET. Reads last 30 days of KB + predictions + plans
and asks Claude to propose ONE concrete, backtestable change. The output
is a JSON spec that hypothesis_runner.py can apply mechanically:

  {
    "id":             "hyp_2026-05-23_a4f9",
    "date":           "2026-05-23",
    "title":          "Raise ADX trend threshold to filter weak trends",
    "rationale":      "...",
    "module":         "signals.regime_detector",
    "var":            "ADX_TREND_MIN",
    "current_value":  25.0,
    "proposed_value": 27.0,
    "expected_impact": "fewer trending signals, higher win rate, lower trade count",
    "confidence":     0.6,
    "status":         "proposed"
  }

Allowed (module, var) targets are constrained -- the engine cannot
propose arbitrary code edits, only knob-twists on already-tunable params.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.knowledge_base import KnowledgeBase, KBEntry
from learning.predictions    import PredictionLog
from journal.plan_logger     import PlanLogger


CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-5-20250929"

# Whitelist of (module_path, var_name) pairs the engine may target.
# Add to this list as you make more thresholds first-class tunables.
TUNABLE_PARAMS = {
    ("signals.regime_detector", "ADX_TREND_MIN"):          {"type": "float", "min": 15.0, "max": 35.0},
    ("signals.regime_detector", "VIX_CALM_MAX"):           {"type": "float", "min": 12.0, "max": 22.0},
    ("signals.regime_detector", "EXTENDED_TREND_MAX_PCT"): {"type": "float", "min": 5.0,  "max": 15.0},
    ("config",                  "SCORE_ALERT_MINIMUM"):    {"type": "int",   "min": 30,   "max": 75},
    ("config",                  "SCORE_HIGH_CONVICTION"):  {"type": "int",   "min": 55,   "max": 90},
    ("config",                  "MIN_RISK_REWARD_RATIO"):  {"type": "float", "min": 1.0,  "max": 3.0},
    ("config",                  "IC_RANGE_THRESHOLD_PCT"): {"type": "float", "min": 1.5,  "max": 4.0},
}

ENGINE_SYSTEM = """You are the trading assistant's hypothesis-generation module.

Given the assistant's recent self-learning data, propose ONE concrete,
testable change to a tunable parameter. The change must:

  1. Target one of the whitelisted parameters (you will be given the list).
  2. Have a numerical proposed value within the parameter's allowed range.
  3. Be motivated by *specific* evidence in the KB / prediction history --
     not a guess.
  4. Have an expected direction of impact you can state in one sentence.

If the data does not support any change with confidence > 0.4, return
status="none" and explain why.

Return ONLY a single JSON object matching this schema:

{
  "status":         "propose | none",
  "title":          "short imperative, e.g. 'Raise ADX_TREND_MIN to 27'",
  "rationale":      "2-4 sentences grounded in specific KB / accuracy data",
  "module":         "signals.regime_detector | config",
  "var":            "exact param name from the whitelist",
  "current_value":  number,
  "proposed_value": number,
  "expected_impact": "one-sentence prediction of effect on win rate / trade count / Sharpe",
  "confidence":     0.0 to 1.0
}"""


class HypothesisEngine:
    """Generates one weekly hypothesis from accumulated learning."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        prediction_log: PredictionLog | None = None,
        plan_logger:    PlanLogger    | None = None,
        api_key:        str | None    = None,
    ):
        self.kb      = knowledge_base or KnowledgeBase()
        self.preds   = prediction_log or PredictionLog()
        self.plans   = plan_logger    or PlanLogger()
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        os.makedirs(os.path.join(config.LOG_DIR, "learning", "hypotheses"), exist_ok=True)

    # ── MAIN ──────────────────────────────────────────

    def propose_weekly(self, today: date | None = None) -> dict | None:
        today     = today or date.today()
        today_str = today.isoformat()

        ctx = {
            "date":             today_str,
            "rolling_accuracy": self.preds.accuracy(n=60),
            "recent_kb":        self.kb.recent(days=30),
            "recent_plans":     self.plans.get_recent(days=30),
            "kb_stats":         self.kb.stats(),
            "tunable_params":   self._tunable_payload(),
        }

        reply = self._call_claude(self._build_prompt(ctx))
        parsed, parse_err = self._parse_reply(reply)

        if parsed is None:
            logger.warning(f"HypothesisEngine: parse failed -- {parse_err}")
            return None

        if parsed.get("status") != "propose":
            logger.info(
                f"HypothesisEngine: no proposal this week -- "
                f"{parsed.get('rationale','(no reason given)')[:160]}"
            )
            self._save_no_proposal(today_str, parsed)
            return None

        if not self._validate(parsed):
            logger.warning("HypothesisEngine: proposed change failed validation")
            return None

        spec = self._to_spec(today_str, parsed)
        self._save_spec(spec)

        self.kb.append(KBEntry(
            date       = today_str,
            category   = "hypothesis",
            claim      = spec["title"],
            evidence   = spec["rationale"],
            confidence = spec["confidence"],
            source     = "hypothesis_engine",
            tags       = ["proposed", spec["module"], spec["var"]],
        ))
        logger.info(f"HypothesisEngine: proposed {spec['id']} -- {spec['title']}")
        return spec

    # ── HELPERS ───────────────────────────────────────

    @staticmethod
    def _tunable_payload() -> list[dict]:
        return [
            {"module": m, "var": v, **rules}
            for (m, v), rules in TUNABLE_PARAMS.items()
        ]

    def _build_prompt(self, ctx: dict) -> str:
        return (
            f"DATE: {ctx['date']}\n\n"
            f"TUNABLE PARAMETERS (you must pick from this list):\n"
            f"{json.dumps(ctx['tunable_params'], indent=2)}\n\n"
            f"ROLLING 60-DAY ACCURACY:\n{json.dumps(ctx['rolling_accuracy'], indent=2)}\n\n"
            f"KB STATS:\n{json.dumps(ctx['kb_stats'], indent=2)}\n\n"
            f"RECENT KB ENTRIES (last 30 days):\n"
            f"{json.dumps(ctx['recent_kb'], indent=2)}\n\n"
            f"RECENT PLANS (last 30 days, summarised):\n"
            f"{json.dumps(self._summarise_plans(ctx['recent_plans']), indent=2)}\n\n"
            f"Propose now."
        )

    @staticmethod
    def _summarise_plans(plans: list[dict]) -> list[dict]:
        out = []
        for p in plans[-30:]:
            out.append({
                "date":      p.get("date"),
                "regime":    p.get("regime"),
                "executed":  p.get("executed"),
                "strategy":  p.get("strategy"),
                "rr_ratio":  p.get("rr_ratio"),
            })
        return out

    def _call_claude(self, prompt: str) -> str:
        if not self.api_key:
            logger.warning("HypothesisEngine: ANTHROPIC_API_KEY missing -- skipping")
            return ""
        import requests
        try:
            resp = requests.post(
                CLAUDE_API_URL,
                headers = {
                    "Content-Type":      "application/json",
                    "x-api-key":         self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json = {
                    "model":      CLAUDE_MODEL,
                    "max_tokens": 1200,
                    "system":     ENGINE_SYSTEM,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout = 60,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
        except Exception as e:
            logger.error(f"HypothesisEngine Claude call failed: {e}")
            return ""

    @staticmethod
    def _parse_reply(text: str) -> tuple[dict | None, str | None]:
        if not text:
            return None, "empty"
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None, "no JSON"
        try:
            return json.loads(m.group(0)), None
        except json.JSONDecodeError as e:
            return None, str(e)

    @staticmethod
    def _validate(parsed: dict) -> bool:
        try:
            module = parsed["module"]
            var    = parsed["var"]
            proposed = float(parsed["proposed_value"])
        except (KeyError, TypeError, ValueError):
            return False
        rules = TUNABLE_PARAMS.get((module, var))
        if rules is None:
            return False
        if not (rules["min"] <= proposed <= rules["max"]):
            return False
        if rules["type"] == "int" and proposed != int(proposed):
            return False
        return True

    @staticmethod
    def _to_spec(today_str: str, parsed: dict) -> dict:
        return {
            "id":              f"hyp_{today_str}_{uuid.uuid4().hex[:4]}",
            "date":            today_str,
            "title":           parsed.get("title", ""),
            "rationale":       parsed.get("rationale", ""),
            "module":          parsed["module"],
            "var":             parsed["var"],
            "current_value":   parsed.get("current_value"),
            "proposed_value":  parsed["proposed_value"],
            "expected_impact": parsed.get("expected_impact", ""),
            "confidence":      float(parsed.get("confidence", 0.5)),
            "status":          "proposed",
            "backtest":        None,
        }

    def _save_spec(self, spec: dict):
        path = os.path.join(
            config.LOG_DIR, "learning", "hypotheses", f"{spec['id']}.json"
        )
        with open(path, "w") as f:
            json.dump(spec, f, indent=2)

    def _save_no_proposal(self, today_str: str, parsed: dict):
        path = os.path.join(
            config.LOG_DIR, "learning", "hypotheses",
            f"{today_str}_no_proposal.json"
        )
        with open(path, "w") as f:
            json.dump(parsed, f, indent=2)

    # ── DISCOVERY ─────────────────────────────────────

    def list_pending(self) -> list[dict]:
        hdir = os.path.join(config.LOG_DIR, "learning", "hypotheses")
        if not os.path.isdir(hdir):
            return []
        out = []
        for fn in sorted(os.listdir(hdir)):
            if not fn.startswith("hyp_") or not fn.endswith(".json"):
                continue
            with open(os.path.join(hdir, fn)) as f:
                spec = json.load(f)
            if spec.get("status") == "proposed":
                out.append(spec)
        return out
