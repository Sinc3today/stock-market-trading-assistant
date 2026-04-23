"""
signals/spy_daily_strategy.py — SPY Daily Play Orchestrator

The single entry point for the daily SPY decision.
Pulls regime → decides if/what to trade → builds the option structure
→ returns one clean PlayCard the rest of the system consumes.

Pipeline:
    1. Fetch SPY daily bars (via existing PolygonClient)
    2. Fetch VIX + IVR (via VIXClient / IVRClient — see data/ stubs)
    3. RegimeDetector.classify()  → tells us the play
    4. If tradeable: build option legs via existing OptionsLayer
    5. Return PlayCard → Discord, dashboard, journal

Usage:
    from signals.spy_daily_strategy import SPYDailyStrategy
    play = SPYDailyStrategy(polygon, vix_client, ivr_client).build_today()
    if play["tradeable"]:
        post_message_sync(play["discord_message"])
"""

from __future__ import annotations

import os
import sys

# ── Path resolution (matches every other module in this project) ──
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass, asdict
from datetime import date

import config
from loguru import logger

from signals.regime_detector import RegimeDetector, Regime, RegimeResult
from signals.options_layer   import OptionsLayer


# ─────────────────────────────────────────
# PLAYCARD — unified output payload
# ─────────────────────────────────────────

@dataclass
class PlayCard:
    date:             str
    tradeable:        bool
    regime:           str
    play:             str
    confidence:       float
    reasons:          list[str]
    metrics:          dict
    options:          dict   # OptionsLayer output (empty dict if skip day)
    discord_message:  str
    plan_payload:     dict   # written to journal/plan_logger — NOT a real fill


# ─────────────────────────────────────────
# STRATEGY ORCHESTRATOR
# ─────────────────────────────────────────

class SPYDailyStrategy:
    """
    Runs the full daily SPY options workflow.
    Designed to be called once per day at ~9:15 AM ET.

    All data clients are injected so the class is unit-testable
    without live API calls.
    """

    def __init__(
        self,
        polygon_client = None,   # data.polygon_client.PolygonClient
        vix_client     = None,   # data.vix_client.VIXClient  (stub — build next)
        ivr_client     = None,   # data.ivr_client.IVRClient  (stub — build next)
        event_calendar = None,   # list[date] — FOMC, CPI, NFP, OPEX dates
    ):
        self.polygon  = polygon_client
        self.vix      = vix_client
        self.ivr      = ivr_client
        self.detector = RegimeDetector(event_calendar=event_calendar)
        self.options  = OptionsLayer()

    # ─────────────────────────────────────────
    # MAIN ENTRY
    # ─────────────────────────────────────────

    def build_today(self, today: date | None = None) -> dict:
        """
        Build today's SPY play.
        Returns a plain dict (from PlayCard.asdict) for easy JSON serialisation.
        """
        today = today or date.today()
        logger.info(f"SPY daily strategy — building play for {today}")

        spy_df      = self._fetch_spy_daily()
        vix_current = self._fetch_vix()
        ivr_current = self._fetch_ivr()

        regime_result = self.detector.classify(
            spy_daily_df = spy_df,
            vix_current  = vix_current,
            ivr_current  = ivr_current,
            today        = today,
        )
        logger.info(f"Regime: {regime_result.regime.value} | {regime_result.play}")

        if not regime_result.tradeable:
            return asdict(self._skip_card(today, regime_result))

        # ── Build option structure ─────────────────────────────
        spy_close            = regime_result.metrics["spy_close"]
        direction, tgt, stop = self._direction_and_levels(
            regime_result.regime, spy_close
        )

        # We pass a synthetic score_result so OptionsLayer is happy.
        # The regime classification IS our conviction signal.
        score_result = {
            "final_score": 85,
            "direction":   direction,
            "tier":        "regime_driven",
        }

        options_payload = self.options.analyze(
            ticker       = "SPY",
            score_result = score_result,
            stock_price  = spy_close,
            target       = tgt,
            stop         = stop,
            mode         = "swing",
            iv_rank      = ivr_current,
        )

        card = PlayCard(
            date            = today.isoformat(),
            tradeable       = options_payload.get("tradeable", False),
            regime          = regime_result.regime.value,
            play            = regime_result.play,
            confidence      = regime_result.confidence,
            reasons         = regime_result.reasons,
            metrics         = regime_result.metrics,
            options         = options_payload,
            discord_message = self._format_discord(today, regime_result, options_payload),
            plan_payload    = self._format_plan(today, regime_result, options_payload),
        )

        logger.info(
            f"Play built: {card.play} | "
            f"Strategy: {options_payload.get('strategy')} | "
            f"Tradeable: {card.tradeable}"
        )
        return asdict(card)

    # ─────────────────────────────────────────
    # REGIME → DIRECTION + LEVELS
    # ─────────────────────────────────────────

    @staticmethod
    def _direction_and_levels(
        regime: Regime, spy_close: float
    ) -> tuple[str, float, float]:
        """
        Map regime to (direction, target, stop).
        For spreads the real risk is defined by the strikes themselves —
        these levels are bookkeeping for the journal.
        """
        if regime in (Regime.TRENDING_UP_CALM, Regime.TRENDING_HIGH_VOL):
            return "bullish", round(spy_close * 1.02, 2), round(spy_close * 0.98, 2)
        if regime == Regime.TRENDING_DOWN_CALM:
            return "bearish", round(spy_close * 0.98, 2), round(spy_close * 1.02, 2)
        # CHOPPY — neutral, condor has no directional target
        return "neutral", spy_close, spy_close

    # ─────────────────────────────────────────
    # DATA FETCHERS
    # ─────────────────────────────────────────

    def _fetch_spy_daily(self):
        """Fetch SPY daily bars using the project's PolygonClient."""
        if self.polygon is None:
            raise RuntimeError(
                "PolygonClient not injected. "
                "Pass polygon_client=PolygonClient() when constructing SPYDailyStrategy."
            )
        # Uses config.SWING_PRIMARY_TIMEFRAME ("day") — same as swing_scanner.py
        return self.polygon.get_bars(
            "SPY",
            timeframe = config.SWING_PRIMARY_TIMEFRAME,
            limit     = 300,
            days_back = 400,
        )

    def _fetch_vix(self) -> float:
        """Fetch current VIX value."""
        if self.vix is None:
            raise RuntimeError(
                "VIXClient not injected. "
                "Build data/vix_client.py and pass vix_client= at construction."
            )
        return self.vix.get_current()

    def _fetch_ivr(self) -> float:
        """Fetch SPY IV Rank (0–100)."""
        if self.ivr is None:
            raise RuntimeError(
                "IVRClient not injected. "
                "Build data/ivr_client.py and pass ivr_client= at construction."
            )
        return self.ivr.get_iv_rank("SPY")

    # ─────────────────────────────────────────
    # SKIP CARD
    # ─────────────────────────────────────────

    def _skip_card(self, today: date, rr: RegimeResult) -> PlayCard:
        msg = (
            f"🚫 **SPY DAILY — NO TRADE** ({today.isoformat()})\n"
            f"Regime: `{rr.regime.value}`\n"
            f"Reason: {rr.play}\n"
            + "\n".join(f"  • {r}" for r in rr.reasons)
        )
        return PlayCard(
            date            = today.isoformat(),
            tradeable       = False,
            regime          = rr.regime.value,
            play            = rr.play,
            confidence      = rr.confidence,
            reasons         = rr.reasons,
            metrics         = rr.metrics,
            options         = {},
            discord_message = msg,
            plan_payload    = {
                "date":   today.isoformat(),
                "ticker": "SPY",
                "action": "SKIP",
                "regime": rr.regime.value,
                "reason": rr.play,
            },
        )

    # ─────────────────────────────────────────
    # DISCORD FORMAT
    # ─────────────────────────────────────────

    @staticmethod
    def _format_discord(
        today: date, rr: RegimeResult, opts: dict
    ) -> str:
        m = rr.metrics
        header = (
            f"📈 **SPY DAILY PLAY** — {today.isoformat()}\n"
            f"Regime:     `{rr.regime.value}` (conf {rr.confidence:.0%})\n"
            f"Play:       **{rr.play}**\n"
            f"VIX={m.get('vix')}  "
            f"IVR={m.get('ivr')}  "
            f"ADX={m.get('adx')}  "
            f"SPY={m.get('spy_close')} "
            f"({m.get('ma200_dist_%'):+}% vs 200MA)\n"
            f"\n_Why this play:_\n"
        )
        reasons = "\n".join(f"  • {r}" for r in rr.reasons)
        return header + reasons + opts.get("discord_addon", "")

    # ─────────────────────────────────────────
    # PLAN PAYLOAD — goes to PlanLogger, NOT TradeRecorder
    # ─────────────────────────────────────────

    @staticmethod
    def _format_plan(
        today: date, rr: RegimeResult, opts: dict
    ) -> dict:
        """
        Structured dict written to logs/spy_daily_plans.json by PlanLogger.
        Separate from TradeRecorder — a plan ≠ a filled trade.
        """
        return {
            "date":             today.isoformat(),
            "ticker":           "SPY",
            "regime":           rr.regime.value,
            "play":             rr.play,
            "confidence":       rr.confidence,
            "strategy":         opts.get("strategy"),
            "legs":             opts.get("legs", []),
            "max_profit":       opts.get("max_profit"),
            "max_loss":         opts.get("max_loss"),
            "rr_ratio":         opts.get("rr_ratio"),
            "recommended_dte":  opts.get("recommended_dte"),
            "exit_rule":        opts.get("exit_rule"),
            "regime_metrics":   rr.metrics,
            "thesis":           " | ".join(rr.reasons),
            "executed":         False,   # flip to True when you actually fill
            "trade_id":         None,    # link to TradeRecorder ID after fill
        }
