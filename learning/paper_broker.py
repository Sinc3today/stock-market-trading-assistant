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
from datetime import date, datetime

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

import config
from journal.trade_recorder import TradeRecorder
from journal.plan_logger    import PlanLogger
from learning.predictions   import PredictionLog, Prediction


AUTO_TAG = "[AUTO-PAPER]"

# Structured marker for bot-generated paper trades. Preferred over the legacy
# AUTO_TAG-in-notes substring check, which broke when the event-driven entry
# path wrote the date inside the brackets ("[AUTO-PAPER 2026-05-27] ...").
AUTO_SOURCE = "auto-paper"


def is_auto_paper(trade: dict) -> bool:
    """True if `trade` is a bot-generated paper position.

    Prefers the structured `source` field; falls back to the legacy
    `[AUTO-PAPER]` notes tag for trades recorded before `source` existed.
    """
    if trade.get("source") == AUTO_SOURCE:
        return True
    return AUTO_TAG in (trade.get("notes_entry") or "")


# ── Multi-position concurrency caps ─────────────────────────────────────────
# Per-book limits on open paper positions. Disciplined book is tighter (it's
# the bot's real-money proxy); learning book is looser (it's sample-gathering).
# Used by execute() and execute_signal() to gate new openings.
MAX_CONCURRENT_DISCIPLINED = 3
MAX_CONCURRENT_LEARNING    = 6

# A plan only opens if it carries one of these real option structures. Anything
# else (e.g. strategy "none" when the brief couldn't price a play) logs the
# prediction but opens nothing.
_VALID_STRATEGIES = {"debit_spread", "credit_spread", "iron_condor", "single_leg"}


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

    # ── HELPERS ───────────────────────────────────────

    @staticmethod
    def _bucket_key(bucket) -> str:
        """0DTE shares the 1-3DTE pool; legacy untagged trades are 45DTE."""
        if bucket in ("0DTE", "1-3DTE"):
            return "1-3DTE"
        return bucket or "45DTE"

    def _bucket_cap_state(self, bucket) -> tuple:
        """-> (pool_key, cap, currently_open_in_pool) for a disciplined open."""
        key = self._bucket_key(bucket)
        slots = getattr(config, "DISCIPLINED_BUCKET_SLOTS", None) or {}
        if key in slots:
            cap = slots[key]
        elif key == "1-3DTE":
            cap = getattr(config, "MAX_CONCURRENT_SHORT_DTE", 1)
        else:
            cap = MAX_CONCURRENT_DISCIPLINED
        open_n = sum(1 for t in self.trades.get_open_trades()
                     if (t.get("book") or "disciplined") == "disciplined"
                     and self._bucket_key(t.get("dte_bucket")) == key)
        return key, cap, open_n

    def _open_count_by_book(self, book: str) -> int:
        """Count currently-open paper trades tagged with the given book.
        Trades that lack a book field (legacy untagged) are treated as
        'disciplined' for the count, since that's all the bot historically
        produced."""
        n = 0
        for t in self.trades.get_all_trades():
            if t.get("outcome") != "open":
                continue
            t_book = t.get("book") or "disciplined"   # legacy untagged ↦ disciplined
            if t_book == book:
                n += 1
        return n

    def _entry_count_today_by_combo(self, strategy: str, dte_bucket: str) -> int:
        """Count trades opened TODAY (in US/Eastern) for the given
        (strategy, dte_bucket) combo. Used by the Phase 3 intraday entry
        router to enforce INTRADAY_PER_COMBO_DAILY_CAP."""
        from datetime import datetime
        import pytz
        today_et = datetime.now(pytz.timezone("US/Eastern")).date().isoformat()

        n = 0
        for t in self.trades.get_trades_by(strategy=strategy, dte_bucket=dte_bucket):
            entry_str = (
                t.get("entry_time")
                or t.get("entry_date")
                or t.get("date")
                or ""
            )
            if entry_str.startswith(today_et):
                n += 1
        return n

    # ── MAIN ──────────────────────────────────────────

    def execute_today(self) -> dict:
        """
        Scheduler entry point: read today's plan from PlanLogger (saved
        by the 09:15 premarket job) and process it. The daily PREDICTION is
        always logged (Standing Rule 15); only the OPEN is gated by the entry
        window — that gate lives in execute().
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
                "forecast":   plan.get("forecast"),
                "options":    {},
            }
        # tradeable ONLY when the plan carries a real options structure. A
        # non-SKIP plan can still have no structure (strategy "none" / no legs)
        # when the brief couldn't price one — those must NOT open a placeholder
        # position. (regression: 2026-06-12 choppy_transition opened $1.00 junk.)
        strategy = plan.get("strategy")
        legs     = plan.get("legs", []) or []
        has_structure = (strategy in _VALID_STRATEGIES) and bool(legs)
        return {
            "date":       plan.get("date"),
            "tradeable":  has_structure,
            "regime":     plan.get("regime", "unknown"),
            "confidence": float(plan.get("confidence", 0.0) or 0.0),
            "reasons":    [s.strip() for s in (plan.get("thesis", "") or "").split("|") if s.strip()],
            "metrics":    plan.get("regime_metrics", {}) or {},
            "options": {
                "strategy":        strategy,
                "legs":            legs,
                "max_profit":      plan.get("max_profit"),
                "max_loss":        plan.get("max_loss"),
                "rr_ratio":        plan.get("rr_ratio"),
                "recommended_dte": plan.get("recommended_dte"),
                "exit_rule":       plan.get("exit_rule"),
            },
            "forecast":   plan.get("forecast"),
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

        # Direction is now an INDEPENDENT next-day forecast (price+VIX), not a
        # mirror of the strategy. Fall back to the old strategy-derived direction
        # only if no forecast was attached (older plays / forecast failure).
        forecast = play.get("forecast") or {}
        if forecast.get("direction"):
            direction          = forecast["direction"]
            expected_move_pct  = forecast.get("expected_move_pct")
            pred_reasons       = forecast.get("reasons") or reasons
            pred_confidence    = float(forecast.get("confidence", confidence))
        else:
            direction          = self._infer_direction(regime, options)
            expected_move_pct  = None
            pred_reasons       = reasons
            pred_confidence    = confidence

        pred = Prediction(
            date             = today_str,
            regime           = regime,
            direction        = direction,
            tradeable        = tradeable,
            entry_spy        = float(entry_spy) if entry_spy is not None else None,
            predicted_target = self._level(options, "target") or self._move_from_metrics(metrics, direction, +0.01),
            predicted_stop   = self._level(options, "stop")   or self._move_from_metrics(metrics, direction, -0.01),
            confidence       = pred_confidence,
            reasons          = pred_reasons,
            expected_move_pct = expected_move_pct,
            strategy         = (play.get("options") or {}).get("strategy"),
            dte_bucket       = "45DTE",
            book             = "disciplined",
        )
        self.predictions.save(pred)

        if not tradeable:
            logger.info(f"PaperBroker: {today_str} skip day, prediction logged only")
            return {"prediction_date": today_str, "trade_id": None, "recorded": False}

        # Defense-in-depth: never open without a real options structure. A
        # tradeable-flagged play with strategy "none" / no legs is a no-structure
        # day — log the prediction, open nothing (no $1.00 placeholder trade).
        if (options.get("strategy") not in _VALID_STRATEGIES) or not (options.get("legs") or []):
            logger.info(f"PaperBroker: {today_str} no valid options structure "
                        f"(strategy={options.get('strategy')!r}) — prediction logged, no open")
            return {"prediction_date": today_str, "trade_id": None,
                    "recorded": False, "skipped": "no_structure"}

        # Prediction is logged above regardless; only the OPEN is gated to the
        # entry window (no opens before 09:45 / after 15:00 ET).
        import config
        if not config.within_entry_window():
            logger.info(f"PaperBroker: {today_str} outside entry window "
                        "(09:45-15:00 ET) — prediction logged, no open")
            return {"prediction_date": today_str, "trade_id": None,
                    "recorded": False, "skipped": "entry_window"}

        # Concentration guard — never stack a new short strike on top of an
        # existing one (disciplined + live books). Three overlapping condors
        # ($700/$705/$713 short puts) taught us a -3% day breaches them all
        # together; the count cap alone doesn't see strike proximity.
        if getattr(config, "ENFORCE_CONCENTRATION_GUARD", True):
            from signals.concentration import proximity_conflicts
            conflicts = proximity_conflicts(
                options.get("legs") or [], self.trades.get_open_trades(),
                pct=getattr(config, "CONCENTRATION_GUARD_PCT", 1.5))
            if conflicts:
                c = conflicts[0]
                logger.info(
                    f"PaperBroker: {today_str} concentration guard — new short "
                    f"{c['type']} {c['new_strike']:g} within {c['distance_pct']}% of "
                    f"open {c['trade_id']} short {c['existing_strike']:g}; no open")
                return {"prediction_date": today_str, "trade_id": None,
                        "recorded": False, "skipped": "concentration_guard"}

        # Daily play lives in the 45DTE rung — per-bucket slots (2026-07-15).
        _, cap45, open_45 = self._bucket_cap_state("45DTE")
        if open_45 >= cap45:
            logger.info(
                f"PaperBroker: 45DTE slots full ({open_45}/{cap45})"
                f" — prediction logged, no new position"
            )
            return {
                "prediction_date": today_str,
                "trade_id":        None,
                "recorded":        False,
                "skipped_reason":  "disciplined_book_cap",
            }

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
            dte_bucket      = "45DTE",
            book            = "disciplined",
            source          = AUTO_SOURCE,
        )

        self.plans.mark_executed(today_str, trade_id)
        logger.info(
            f"PaperBroker: {today_str} {strategy} {direction} "
            f"recorded as trade {trade_id}"
        )
        return {"prediction_date": today_str, "trade_id": trade_id, "recorded": True}

    def execute_signal(self, setup: dict) -> dict:
        """Event-driven entry — Phase 3's intraday scanner will call this when
        a sub-strategy setup fires intraday. Respects per-book concurrency caps.

        setup dict shape:
          {
            "date":        str (today's ISO date),
            "strategy":    str ("call_debit_spread" / "put_debit_spread" / "iron_condor"),
            "dte_bucket":  str ("0DTE" / "1-3DTE"),
            "book":        str ("disciplined" / "learning"),
            "direction":   str ("bullish" / "bearish" / "neutral"),
            "entry_price": float,
            "max_profit":  float,
            "max_loss":    float,
            "legs":        list[dict],
          }
        """
        import config
        if not config.within_entry_window():
            logger.info("PaperBroker.execute_signal: outside entry window "
                        "(09:45-15:00 ET) — no open")
            return {"recorded": False, "skipped": "entry_window"}
        book = setup.get("book", "disciplined")

        if book == "disciplined":
            # Strike-concentration guard (was only on the daily-play path —
            # entries minutes OR days apart on overlapping shorts share one loss).
            if getattr(config, "ENFORCE_CONCENTRATION_GUARD", True):
                from signals.concentration import proximity_conflicts
                conflicts = proximity_conflicts(
                    setup.get("legs") or [], self.trades.get_open_trades(),
                    pct=getattr(config, "CONCENTRATION_GUARD_PCT", 1.5))
                if conflicts:
                    c = conflicts[0]
                    logger.info(f"PaperBroker.execute_signal: concentration guard — "
                                f"short {c['type']} {c['new_strike']:g} within "
                                f"{c['distance_pct']}% of {c['trade_id']}; skipped")
                    return {"trade_id": None, "recorded": False,
                            "skipped": "concentration_guard"}

            # Per-bucket slots (2026-07-15): each DTE rung has its own budget
            # (config.DISCIPLINED_BUCKET_SLOTS) so no rung crowds out another.
            key, cap, open_n = self._bucket_cap_state(setup.get("dte_bucket"))
            if open_n >= cap:
                reason = ("short_dte_slot_cap" if key == "1-3DTE"
                          else "disciplined_book_cap")
                logger.info(f"PaperBroker.execute_signal: {key} slots full "
                            f"({open_n}/{cap}) — skipped")
                return {"trade_id": None, "recorded": False,
                        "skipped_reason": reason}

            # Entry pacing (user: "1-2 trades a day, not close together —
            # back-to-back entries ride the same SPY move").
            if not setup.get("_test_bypass_pacing"):
                opened = self._disciplined_opens_today()
                if len(opened) >= getattr(config, "MAX_DAILY_DISCIPLINED_OPENS", 2):
                    logger.info("PaperBroker.execute_signal: daily open limit "
                                f"reached ({len(opened)}) — skipped")
                    return {"trade_id": None, "recorded": False,
                            "skipped_reason": "daily_open_limit"}
                gap = self._minutes_since_last_disciplined_open(opened)
                spacing = getattr(config, "MIN_ENTRY_SPACING_MIN", 90)
                if gap is not None and gap < spacing:
                    logger.info(f"PaperBroker.execute_signal: last open {gap:.0f}m "
                                f"ago (< {spacing}m spacing) — skipped")
                    return {"trade_id": None, "recorded": False,
                            "skipped_reason": "entry_spacing"}
        else:
            cap = MAX_CONCURRENT_LEARNING
            open_n = self._open_count_by_book(book)
            if open_n >= cap:
                logger.info(
                    f"PaperBroker.execute_signal: {book} cap reached ({open_n}/{cap}) — skipped"
                )
                return {"trade_id": None, "recorded": False,
                        "skipped_reason": f"{book}_book_cap"}

        tid = self.trades.log_entry(
            ticker      = "SPY",
            entry_price = float(setup.get("entry_price", 0.0)),
            size        = 1,
            trade_type  = "option_spread",
            strategy    = setup.get("strategy"),
            direction   = setup.get("direction", "neutral"),
            mode        = "intraday" if setup.get("dte_bucket") in ("0DTE", "1-3DTE") else "swing",
            legs        = setup.get("legs", []),
            max_profit  = setup.get("max_profit"),
            max_loss    = setup.get("max_loss"),
            notes       = f"{AUTO_TAG} event-driven entry {setup.get('date')}",
            dte_bucket  = setup.get("dte_bucket"),
            book        = book,
            source      = AUTO_SOURCE,
        )
        logger.info(
            f"PaperBroker.execute_signal: opened {tid} | "
            f"{setup.get('strategy')} @ {setup.get('dte_bucket')} ({book})"
        )
        return {"trade_id": tid, "recorded": True}

    # ── HELPERS ───────────────────────────────────────

    def _disciplined_opens_today(self) -> list[dict]:
        """Disciplined trades whose entry stamp is today (ET) — the daily-pacing
        sample (open or already closed; an intraday scratch still counts)."""
        today = datetime.now(pytz.timezone("US/Eastern")).date().isoformat()
        return [t for t in self.trades.get_all_trades()
                if t.get("book") == "disciplined"
                and str(t.get("entry_date", "")).startswith(today)]

    @staticmethod
    def _minutes_since_last_disciplined_open(opened_today: list[dict]):
        """Minutes since the most recent disciplined entry today, or None."""
        eastern = pytz.timezone("US/Eastern")
        latest = None
        for t in opened_today:
            try:
                dt = eastern.localize(datetime.strptime(
                    str(t.get("entry_date", "")).replace(" EST", "").strip(),
                    "%Y-%m-%d %I:%M %p"))
            except ValueError:
                continue
            if latest is None or dt > latest:
                latest = dt
        if latest is None:
            return None
        return (datetime.now(eastern) - latest).total_seconds() / 60.0

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
