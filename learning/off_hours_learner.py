"""learning/off_hours_learner.py — Phase 4a item 6.

Weekend learning. Pivot from "near-miss threshold tuning" → "regime-drift
detection". Runs Sunday 10:00 ET.

Pivot rationale: with Phase 3 paper trading live, near-miss tuning is
redundant with hypothesis_engine (Saturday). Regime drift catches the
highest-leverage signal — meta-shifts in market structure that affect
ALL sub-strategies at once — and has always-available data regardless
of trade volume.

Algorithm:
  1. Load 120 trading days of SPY regime classifications.
  2. Split into recent-60d vs prior-60d windows.
  3. Compute regime distribution (% of days per regime) for each window.
  4. Identify shifts ≥REGIME_DRIFT_THRESHOLD_PCT (default 10pts).
  5. Compute feature trends (VIX/ADX/MA200_dist means) for narrative.
  6. Send shifts + trends to Sonnet; persist KB entry as kind=regime_drift.
  7. If <60 trading days available, skip the Claude call.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta

from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.knowledge_base import KnowledgeBase, KBEntry
from data.llm_client          import call_llm


CLAUDE_MODEL = "claude-sonnet-4-6"

LEARNER_SYSTEM = """You are the trading assistant's off-hours regime-drift module.

You receive:
  - A list of regime distribution SHIFTS between a recent-60d window and a
    prior-60d window (each shift = {regime, recent_pct, prior_pct, delta_pct}).
  - Feature trends (VIX/ADX/MA200_dist means for each window).

Your job: interpret the shifts in terms of trading implications. Are we
moving toward more trending regimes? More volatility? Which strategies'
typical conditions are becoming more/less common?

Return ONLY a single JSON object:

{
  "kb_entries": [
    {
      "category":   "market_context",
      "kind":       "regime_drift",
      "claim":      "<= 200 chars",
      "evidence":   "specific deltas and feature trends",
      "confidence": 0.0 to 1.0,
      "tags":       ["regime_drift", ...]
    }
  ]
}"""


# ────────────────────────────────────────────────────────────
#  Pure helpers (importable for tests + reuse)
# ────────────────────────────────────────────────────────────

def compute_distribution(rows: list[dict]) -> dict[str, float]:
    """Return {regime: pct} from a list of {regime: ...} rows."""
    if not rows:
        return {}
    counts: dict[str, int] = {}
    for r in rows:
        rg = r.get("regime")
        if not rg:
            continue
        counts[rg] = counts.get(rg, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: round(v / total * 100, 2) for k, v in counts.items()}


def detect_shifts(prior: dict[str, float], recent: dict[str, float],
                  threshold_pct: float) -> list[dict]:
    """Return list of regime shifts whose abs(delta_pct) >= threshold_pct.

    Each shift: {regime, recent_pct, prior_pct, delta_pct}.
    """
    all_regimes = set(prior) | set(recent)
    shifts = []
    for rg in all_regimes:
        p = prior.get(rg, 0.0)
        r = recent.get(rg, 0.0)
        delta = r - p
        if abs(delta) >= threshold_pct:
            shifts.append({
                "regime":     rg,
                "recent_pct": r,
                "prior_pct":  p,
                "delta_pct":  round(delta, 1),
            })
    return shifts


def compute_feature_trends(rows: list[dict]) -> dict[str, float]:
    """Compute mean VIX/ADX/MA200_dist over the rows."""
    if not rows:
        return {}

    def mean(field):
        vals = [r.get(field) for r in rows if isinstance(r.get(field), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "vix_mean":          mean("vix"),
        "adx_mean":          mean("adx"),
        "ma200_dist_mean":   mean("ma200_dist"),
    }


# ────────────────────────────────────────────────────────────
#  Learner
# ────────────────────────────────────────────────────────────

class OffHoursLearner:
    """Weekend regime-drift detector + Claude pattern-finding."""

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

        rows = self._load_regime_classifications(today)
        recent_days = int(config.REGIME_DRIFT_RECENT_DAYS)
        prior_days  = int(config.REGIME_DRIFT_PRIOR_DAYS)
        need_total  = recent_days + prior_days

        if len(rows) < need_total:
            logger.info(
                f"OffHoursLearner: insufficient history "
                f"({len(rows)} < {need_total}) — skipping"
            )
            return {"date": today_str, "skipped": True, "rows_available": len(rows)}

        rows = sorted(rows, key=lambda r: r["date"])[-need_total:]
        prior  = rows[:prior_days]
        recent = rows[prior_days:]

        prior_dist  = compute_distribution(prior)
        recent_dist = compute_distribution(recent)
        shifts = detect_shifts(prior_dist, recent_dist,
                               threshold_pct=float(config.REGIME_DRIFT_THRESHOLD_PCT))
        trends_prior  = compute_feature_trends(prior)
        trends_recent = compute_feature_trends(recent)

        report = {
            "date":         today_str,
            "window_recent": f"{recent[0]['date']} to {recent[-1]['date']}",
            "window_prior":  f"{prior[0]['date']} to {prior[-1]['date']}",
            "prior_dist":    prior_dist,
            "recent_dist":   recent_dist,
            "shifts":        shifts,
            "feature_trends": {
                "prior":  trends_prior,
                "recent": trends_recent,
            },
            "shift_count":   len(shifts),
        }

        report_path = os.path.join(
            config.LOG_DIR, "learning", "off_hours", f"{today_str}.json"
        )
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        kb_ids = self._ask_claude_for_interpretation(today_str, report)
        return {
            "date":        today_str,
            "shift_count": len(shifts),
            "kb_appended": len(kb_ids),
            "kb_ids":      kb_ids,
            "report_path": report_path,
        }

    # ── REGIME CLASSIFICATION LOAD ────────────────────

    def _load_regime_classifications(self, today: date) -> list[dict]:
        """Load ~170 calendar days of SPY rows and classify each.

        Returns list of dicts {date, regime, vix, adx, ma200_dist} sorted by date.
        """
        try:
            import pandas as pd
            from signals.regime_detector import RegimeDetector
        except Exception as e:
            logger.warning(f"OffHoursLearner: deps missing -- {e}")
            return []

        csv_path = os.path.join("backtests", "spy_history.csv")
        if not os.path.exists(csv_path):
            logger.warning("OffHoursLearner: backtests/spy_history.csv missing")
            return []
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        df.index   = pd.to_datetime(df.index).date
        df = df.sort_index()

        # Look back 170 calendar days to get ~120 trading days
        cutoff = today - timedelta(days=170)
        df = df[df.index >= cutoff]
        if len(df) < 60:
            return []

        # Load VIX history (best-effort)
        vix_lookup: dict = {}
        try:
            from data.vix_client import VIXClient
            vdf = VIXClient().get_history(days=180)
            if vdf is not None and len(vdf) > 0:
                vix_lookup = {d: float(c) for d, c in vdf["close"].items()}
        except Exception:
            pass

        detector = RegimeDetector()
        rows: list[dict] = []
        dates = sorted(df.index)
        for i, d in enumerate(dates):
            if i < 210:    # need lookback for indicators
                continue
            hist = df.loc[dates[max(0, i-250):i]].copy()
            hist.index = pd.to_datetime(hist.index)
            vix_today = vix_lookup.get(d, 16.0)
            try:
                r = detector.classify(
                    spy_daily_df=hist,
                    vix_current=vix_today,
                    ivr_current=30.0,
                    today=d,
                )
            except Exception:
                continue
            rows.append({
                "date":       d,
                "regime":     r.regime.value,
                "vix":        vix_today,
                "adx":        r.metrics.get("adx", 0.0),
                "ma200_dist": r.metrics.get("ma200_dist_%", 0.0),
            })
        return rows

    # ── CLAUDE ────────────────────────────────────────

    def _ask_claude_for_interpretation(
        self, today_str: str, report: dict
    ) -> list[str]:
        if not self.api_key:
            logger.info("OffHoursLearner: no API key -- skipping Claude pass")
            return []
        try:
            text = call_llm(
                system               = LEARNER_SYSTEM,
                user                 = (
                    f"WINDOWS: {report['window_prior']} (prior) vs "
                    f"{report['window_recent']} (recent)\n\n"
                    f"PRIOR DISTRIBUTION:\n{json.dumps(report['prior_dist'], indent=2)}\n\n"
                    f"RECENT DISTRIBUTION:\n{json.dumps(report['recent_dist'], indent=2)}\n\n"
                    f"SHIFTS (|delta| >= {config.REGIME_DRIFT_THRESHOLD_PCT}%):\n"
                    f"{json.dumps(report['shifts'], indent=2)}\n\n"
                    f"FEATURE TRENDS:\n{json.dumps(report['feature_trends'], indent=2)}\n\n"
                    f"Produce JSON now."
                ),
                anthropic_model      = CLAUDE_MODEL,
                api_key              = self.api_key,
                max_tokens           = 1000,
                cache_static_system  = True,
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
                    category   = raw.get("category", "market_context"),
                    claim      = raw.get("claim", "")[:500],
                    evidence   = raw.get("evidence", "")[:1000],
                    confidence = float(raw.get("confidence", 0.65)),
                    source     = "off_hours_learner",
                    tags       = list(raw.get("tags") or [])[:8] + ["regime_drift"],
                )))
            except Exception as e:
                logger.warning(f"OffHoursLearner: bad entry skipped -- {e}")
        return ids
