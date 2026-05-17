"""
learning/off_hours_learner.py -- Weekend learning when market is closed.

Runs Sunday 10:00 ET. The goal is to keep the assistant *learning* even
when there are no live trades to score. It does two things:

  1. Replays the last 60 days of SPY history through the *current* regime
     detector and identifies "near-miss" days: days where the regime call
     was confirmed wrong by the next-day move but the prediction was close
     to a boundary (e.g. ADX just barely above the threshold, VIX just
     below).
  2. Asks Claude to look at the near-misses + recent KB and propose
     1-3 *observation* KB entries (NOT hypotheses -- the hypothesis_engine
     handles those). Observations might become hypotheses later.

If Claude isn't available the replay still runs and writes a structured
report so the next session can use it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.knowledge_base import KnowledgeBase, KBEntry


CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-5-20250929"

LEARNER_SYSTEM = """You are the trading assistant's off-hours learning module.

You will receive a list of 'near-miss' days from the recent SPY history --
days where the regime classifier was on the edge of a threshold and the
next-day move went against the call. Look for patterns across them.

Produce 1-3 KB entries (category 'edge_case' or 'market_context') that
describe what these near-misses have in common. Do NOT propose parameter
changes -- those go through the hypothesis_engine.

Return ONLY a single JSON object:

{
  "kb_entries": [
    {
      "category":   "edge_case | market_context",
      "claim":      "<= 200 chars",
      "evidence":   "specific dates / numbers",
      "confidence": 0.0 to 1.0,
      "tags":       ["..."]
    }
  ]
}"""


REPLAY_DAYS = 60

# Buffers that define "near the threshold"
ADX_NEAR_PCT = 0.10   # within 10% of ADX_TREND_MIN
VIX_NEAR_PCT = 0.10   # within 10% of VIX_CALM_MAX


class OffHoursLearner:
    """Weekend replay + Claude pattern-finding."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        api_key:        str | None    = None,
    ):
        self.kb      = knowledge_base or KnowledgeBase()
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    # ── MAIN ──────────────────────────────────────────

    def run(self, today: date | None = None) -> dict:
        today     = today or date.today()
        today_str = today.isoformat()

        near_misses = self._find_near_misses()
        report = {
            "date":            today_str,
            "replay_days":     REPLAY_DAYS,
            "near_miss_count": len(near_misses),
            "near_misses":     near_misses,
        }
        report_path = os.path.join(
            config.LOG_DIR, "learning", "off_hours", f"{today_str}.json"
        )
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        if not near_misses:
            logger.info("OffHoursLearner: no near-misses in replay -- KB unchanged")
            return {"date": today_str, "near_miss_count": 0, "kb_appended": 0}

        kb_ids = self._ask_claude_for_observations(today_str, near_misses)
        logger.info(
            f"OffHoursLearner: {len(near_misses)} near-misses analysed, "
            f"{len(kb_ids)} KB entries appended"
        )
        return {
            "date":            today_str,
            "near_miss_count": len(near_misses),
            "kb_appended":     len(kb_ids),
            "kb_ids":          kb_ids,
            "report_path":     report_path,
        }

    # ── REPLAY ────────────────────────────────────────

    def _find_near_misses(self) -> list[dict]:
        """
        Walk the local SPY CSV, classify each day with current detector,
        check next-day move, return days where prediction was near a
        threshold AND the next-day move went against the directional call.
        """
        try:
            import pandas as pd
            import signals.regime_detector as rd
            from signals.regime_detector import RegimeDetector, Regime
        except Exception as e:
            logger.warning(f"OffHoursLearner: replay deps missing -- {e}")
            return []

        csv_path = os.path.join("backtests", "spy_history.csv")
        if not os.path.exists(csv_path):
            logger.warning("OffHoursLearner: backtests/spy_history.csv missing")
            return []

        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        df.index   = pd.to_datetime(df.index).date
        df         = df.sort_index()

        cutoff = date.today() - timedelta(days=REPLAY_DAYS + 30)
        df     = df[df.index >= cutoff]
        if len(df) < 50:
            return []

        adx_min = rd.ADX_TREND_MIN
        vix_max = rd.VIX_CALM_MAX
        detector = RegimeDetector()
        dates    = sorted(df.index)

        near: list[dict] = []
        for i, today in enumerate(dates[-REPLAY_DAYS:]):
            idx = dates.index(today)
            if idx < 210 or idx + 1 >= len(dates):
                continue
            hist = df.loc[dates[max(0, idx-250):idx]].copy()
            hist.index = pd.to_datetime(hist.index)

            # Use a flat-ish VIX proxy: we don't have history per-day at zero cost here,
            # so reuse the same default (16) the production backtest falls back to.
            vix_today = 16.0
            ivr_today = 30.0
            try:
                r = detector.classify(
                    spy_daily_df = hist,
                    vix_current  = vix_today,
                    ivr_current  = ivr_today,
                    today        = today,
                )
            except Exception:
                continue

            adx = r.metrics.get("adx", 0.0)
            adx_near = abs(adx - adx_min) / max(adx_min, 1) < ADX_NEAR_PCT
            vix_near = abs(vix_today - vix_max) / max(vix_max, 1) < VIX_NEAR_PCT
            if not (adx_near or vix_near):
                continue

            today_close = float(df.loc[today, "close"])
            tomorrow    = dates[idx + 1]
            tomorrow_close = float(df.loc[tomorrow, "close"])
            move_pct = (tomorrow_close - today_close) / today_close * 100

            # "wrong" call: bullish regime with negative next-day move, or vice versa
            wrong = False
            if r.regime == Regime.TRENDING_UP_CALM   and move_pct < -0.10: wrong = True
            if r.regime == Regime.TRENDING_DOWN_CALM and move_pct > +0.10: wrong = True
            if r.regime == Regime.CHOPPY_LOW_VOL     and abs(move_pct) > 0.50: wrong = True

            if not wrong:
                continue
            near.append({
                "date":     today.isoformat(),
                "regime":   r.regime.value,
                "adx":      adx,
                "vix_used": vix_today,
                "move_pct": round(move_pct, 3),
                "adx_near_threshold": adx_near,
                "vix_near_threshold": vix_near,
            })

        return near

    # ── CLAUDE ────────────────────────────────────────

    def _ask_claude_for_observations(
        self, today_str: str, near_misses: list[dict]
    ) -> list[str]:
        if not self.api_key:
            logger.info("OffHoursLearner: no API key -- skipping Claude pass")
            return []
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
                    "max_tokens": 1000,
                    "system":     LEARNER_SYSTEM,
                    "messages":   [{
                        "role": "user",
                        "content": (
                            f"NEAR-MISSES ({len(near_misses)} days):\n"
                            f"{json.dumps(near_misses, indent=2)}\n\n"
                            f"Produce JSON now."
                        ),
                    }],
                },
                timeout = 60,
            )
            resp.raise_for_status()
            text = "".join(
                b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text"
            )
        except Exception as e:
            logger.error(f"OffHoursLearner Claude failed: {e}")
            return []

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

        ids = []
        for raw in parsed.get("kb_entries", []):
            try:
                ids.append(self.kb.append(KBEntry(
                    date       = today_str,
                    category   = raw.get("category", "edge_case"),
                    claim      = raw.get("claim", "")[:500],
                    evidence   = raw.get("evidence", "")[:1000],
                    confidence = float(raw.get("confidence", 0.4)),
                    source     = "off_hours_learner",
                    tags       = list(raw.get("tags") or [])[:8],
                )))
            except Exception as e:
                logger.warning(f"OffHoursLearner: bad entry skipped -- {e}")
        return ids
