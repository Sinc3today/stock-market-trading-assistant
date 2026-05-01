"""
journal/plan_logger.py — Daily SPY Plan Logger

Stores pre-trade plans produced by SPYDailyStrategy.
A plan is NOT a fill. It lives here until you actually execute,
at which point you flip executed=True and attach a trade_id.

Why separate from TradeRecorder?
    TradeRecorder tracks real filled positions with real P&L.
    PlanLogger tracks what the system recommended each day —
    useful for measuring regime accuracy even on days you skipped.

Usage:
    from journal.plan_logger import PlanLogger
    pl = PlanLogger()
    pl.save_plan(play_card["plan_payload"])        # from SPYDailyStrategy
    pl.mark_executed("2026-04-22", trade_id="A1B2C3")
    history = pl.get_recent(days=30)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
from datetime import date, timedelta
from loguru import logger

import config


class PlanLogger:
    """
    Persists SPY daily plans to logs/spy_daily_plans.json.
    Keeps the last 365 entries (one per trading day max).
    """

    PLANS_FILE = None  # resolved dynamically via _plans_path property

    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)

    @property
    def _plans_path(self) -> str:
        """Resolved at call-time so monkeypatched LOG_DIR works in tests."""
        return os.path.join(config.LOG_DIR, "spy_daily_plans.json")

    # ─────────────────────────────────────────
    # WRITE
    # ─────────────────────────────────────────

    def save_plan(self, plan: dict) -> bool:
        """
        Save a plan payload from SPYDailyStrategy.
        If a plan already exists for today, it is overwritten.
        """
        if not plan or "date" not in plan:
            logger.warning("PlanLogger.save_plan: plan missing 'date' key")
            return False

        plans = self._load()
        # Remove any existing entry for the same date (idempotent)
        plans = [p for p in plans if p.get("date") != plan["date"]]
        plans.append(plan)
        self._save(plans)

        logger.info(
            f"Plan saved: {plan.get('date')} | "
            f"Regime: {plan.get('regime')} | "
            f"Play: {plan.get('play')}"
        )
        return True

    def mark_executed(self, plan_date: str, trade_id: str) -> bool:
        """
        After you actually fill the trade, call this to link the
        TradeRecorder trade_id back to the plan.
        """
        plans = self._load()
        for plan in plans:
            if plan.get("date") == plan_date:
                plan["executed"] = True
                plan["trade_id"] = trade_id
                self._save(plans)
                logger.info(f"Plan {plan_date} marked executed → trade_id: {trade_id}")
                return True

        logger.warning(f"PlanLogger.mark_executed: no plan found for {plan_date}")
        return False

    # ─────────────────────────────────────────
    # READ
    # ─────────────────────────────────────────

    def get_plan(self, plan_date: str) -> dict | None:
        """Return the plan for a specific date, or None."""
        plans = self._load()
        return next((p for p in plans if p.get("date") == plan_date), None)

    def get_today(self) -> dict | None:
        """Return today's plan if it exists."""
        return self.get_plan(date.today().isoformat())

    def get_recent(self, days: int = 30) -> list[dict]:
        """Return all plans from the last N days, newest first."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        plans  = self._load()
        recent = [p for p in plans if p.get("date", "") >= cutoff]
        return sorted(recent, key=lambda p: p["date"], reverse=True)

    def get_stats(self) -> dict:
        """
        Summary stats for the plan log.
        Useful for measuring: how often does the regime say tradeable?
        How often do we actually execute vs skip?
        """
        plans = self._load()
        if not plans:
            return {"total": 0}

        total         = len(plans)
        tradeable     = sum(1 for p in plans if p.get("regime") not in
                            ("event_day", "choppy_high_vol", "unknown") and
                            p.get("action") != "SKIP")
        executed      = sum(1 for p in plans if p.get("executed"))
        skipped       = sum(1 for p in plans if p.get("action") == "SKIP")
        regime_counts = {}
        for p in plans:
            r = p.get("regime", "unknown")
            regime_counts[r] = regime_counts.get(r, 0) + 1

        return {
            "total":          total,
            "tradeable_days": tradeable,
            "skip_days":      skipped,
            "executed":       executed,
            "execution_rate": round(executed / tradeable * 100, 1) if tradeable else 0.0,
            "regime_counts":  regime_counts,
        }

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _load(self) -> list[dict]:
        if not os.path.exists(self._plans_path):
            return []
        try:
            with open(self._plans_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"PlanLogger load error: {e}")
            return []

    def _save(self, plans: list[dict]):
        # Keep last 365 entries
        plans = sorted(plans, key=lambda p: p.get("date", ""))[-365:]
        try:
            with open(self._plans_path, "w") as f:
                json.dump(plans, f, indent=2)
        except OSError as e:
            logger.error(f"PlanLogger save error: {e}")
