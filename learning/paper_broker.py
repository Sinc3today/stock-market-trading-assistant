"""
learning/paper_broker.py -- Auto paper-trade execution.

Called from the 09:15 ET pre-market job after SPYDailyStrategy.build_today()
has produced a PlayCard. The broker:

  1. Always logs a Prediction (skip days included -- we learn from skips too).
  2. If tradeable, logs a paper position via TradeRecorder with notes tagged
     "[AUTO-PAPER]" so it's distinguishable from real fills.
  3. Marks the plan executed and links the trade_id back.

Sizing: always 1 contract (or 1 share for stock). This is a learning
environment, not capital deployment -- the goal is signal quality data,
not P&L.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

from journal.trade_recorder import TradeRecorder
from journal.plan_logger    import PlanLogger
from learning.predictions   import PredictionLog, Prediction


AUTO_TAG = "[AUTO-PAPER]"


class PaperBroker:
    """Records a paper position from a daily PlayCard."""

    def __init__(
        self,
        trade_recorder:  TradeRecorder  | None = None,
        plan_logger:     PlanLogger     | None = None,
        prediction_log:  PredictionLog  | None = None,
    ):
        self.trades      = trade_recorder  or TradeRecorder()
        self.plans       = plan_logger     or PlanLogger()
        self.predictions = prediction_log  or PredictionLog()

    # ── MAIN ──────────────────────────────────────────

    def execute_today(self) -> dict:
        """
        Scheduler entry point: read today's plan from PlanLogger (saved
        by the 09:15 premarket job) and process it.
        """
        plan = self.plans.get_today()
        if not plan:
            logger.info("PaperBroker.execute_today: no plan for today, nothing to do")
            return {"prediction_date": None, "trade_id": None, "recorded": False}
        return self.execute(self._plan_to_play(plan))

    @staticmethod
    def _plan_to_play(plan: dict) -> dict:
        """
        Re-shape a saved plan dict (from PlanLogger) into the PlayCard
        format execute() expects. Handles both the tradeable plan format
        and the skip plan format.
        """
        if plan.get("action") == "SKIP":
            return {
                "date":       plan.get("date"),
                "tradeable":  False,
                "regime":     plan.get("regime", "unknown"),
                "confidence": 0.0,
                "reasons":    [plan.get("reason", "skip")],
                # Carry the decision-time metrics so the prediction keeps a
                # baseline price (entry_spy) for skip-scoring downstream.
                "metrics":    plan.get("regime_metrics", {}) or {},
                "intended_direction": plan.get("intended_direction"),
                "options":    {},
            }
        return {
            "date":       plan.get("date"),
            "tradeable":  True,
            "regime":     plan.get("regime", "unknown"),
            "confidence": float(plan.get("confidence", 0.0) or 0.0),
            "reasons":    [s.strip() for s in (plan.get("thesis", "") or "").split("|") if s.strip()],
            "metrics":    plan.get("regime_metrics", {}) or {},
            "options": {
                "strategy":        plan.get("strategy"),
                "legs":            plan.get("legs", []),
                "max_profit":      plan.get("max_profit"),
                "max_loss":        plan.get("max_loss"),
                "rr_ratio":        plan.get("rr_ratio"),
                "recommended_dte": plan.get("recommended_dte"),
                "exit_rule":       plan.get("exit_rule"),
            },
        }

    def execute(self, play: dict) -> dict:
        """
        Process a PlayCard dict (output of SPYDailyStrategy.build_today()).
        Returns {prediction_date, trade_id (or None), recorded (bool)}.
        """
        today_str  = play.get("date") or date.today().isoformat()
        regime     = play.get("regime", "unknown")
        tradeable  = bool(play.get("tradeable"))
        confidence = float(play.get("confidence", 0.0))
        metrics    = play.get("metrics", {}) or {}
        options    = play.get("options", {})  or {}
        reasons    = play.get("reasons", [])  or []
        entry_spy  = metrics.get("spy_close")

        direction = self._infer_direction(regime, options)

        pred = Prediction(
            date             = today_str,
            regime           = regime,
            direction        = direction,
            tradeable        = tradeable,
            entry_spy        = float(entry_spy) if entry_spy is not None else None,
            predicted_target = self._level(options, "target") or self._move_from_metrics(metrics, direction, +0.01),
            predicted_stop   = self._level(options, "stop")   or self._move_from_metrics(metrics, direction, -0.01),
            confidence       = confidence,
            reasons          = reasons,
        )
        self.predictions.save(pred)

        if not tradeable:
            logger.info(f"PaperBroker: {today_str} skip day, prediction logged only")
            return {"prediction_date": today_str, "trade_id": None, "recorded": False}

        legs       = options.get("legs", []) or []
        strategy   = options.get("strategy", "single_leg")
        max_profit = options.get("max_profit")
        max_loss   = options.get("max_loss")
        entry_px   = self._spread_price(options)

        notes = (
            f"{AUTO_TAG} regime={regime} confidence={confidence:.2f} "
            f"thesis={' | '.join(reasons[:3])}"
        )

        trade_id = self.trades.log_entry(
            ticker          = "SPY",
            entry_price     = entry_px,
            size            = 1,
            trade_type      = strategy if strategy in {"debit_spread","credit_spread","iron_condor","single_leg"} else "single_leg",
            strategy        = strategy,
            direction       = direction if direction in ("bullish","bearish") else "bullish",
            mode            = "swing",
            legs            = legs,
            max_profit      = self._numeric(max_profit),
            max_loss        = self._numeric(max_loss),
            alert_timestamp = today_str,
            alert_score     = int(round(confidence * 100)),
            notes           = notes,
        )

        self.plans.mark_executed(today_str, trade_id)
        logger.info(
            f"PaperBroker: {today_str} {strategy} {direction} "
            f"recorded as trade {trade_id}"
        )
        return {"prediction_date": today_str, "trade_id": trade_id, "recorded": True}

    # ── HELPERS ───────────────────────────────────────

    @staticmethod
    def _infer_direction(regime: str, options: dict) -> str:
        opt_dir = (options.get("direction") or "").lower()
        if opt_dir in ("bullish", "bearish", "neutral"):
            return opt_dir
        regime = (regime or "").lower()
        if "up" in regime:
            return "bullish"
        if "down" in regime:
            return "bearish"
        if "choppy" in regime or "condor" in (options.get("strategy") or ""):
            return "neutral"
        return "neutral"

    @staticmethod
    def _level(options: dict, key: str):
        v = options.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _move_from_metrics(metrics: dict, direction: str, pct: float):
        """Fallback target/stop if options layer didn't supply one."""
        spy = metrics.get("spy_close")
        if spy is None:
            return None
        if direction == "bullish":
            return round(spy * (1 + pct), 2)
        if direction == "bearish":
            return round(spy * (1 - pct), 2)
        return round(float(spy), 2)

    @staticmethod
    def _spread_price(options: dict) -> float:
        """
        Best-effort entry price for the journal: prefer explicit net_debit /
        net_credit; fall back to first leg's price; else 1.00 as placeholder.
        """
        for key in ("net_debit", "net_credit", "entry_price", "mid"):
            v = options.get(key)
            if v is not None:
                try:
                    return abs(float(v))
                except (TypeError, ValueError):
                    pass
        legs = options.get("legs") or []
        if legs and isinstance(legs[0], dict):
            for key in ("price", "premium", "mid"):
                if key in legs[0]:
                    try:
                        return abs(float(legs[0][key]))
                    except (TypeError, ValueError):
                        pass
        return 1.00

    @staticmethod
    def _numeric(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace("$", "").replace(",", "").strip()
        # handle "~$300" or "300.00"
        s = s.lstrip("~").strip()
        try:
            return float(s)
        except ValueError:
            return None
