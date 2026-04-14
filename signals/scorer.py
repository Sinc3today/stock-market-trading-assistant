"""
signals/scorer.py — Confidence Score Engine
Takes all indicator results and produces a single 0-100 confidence score.

Scoring structure:
    Trend Layer  (MA):              0-35 pts
    Setup Layer  (Donchian + RSI):  0-35 pts  (Donchian 0-15, RSI 0-12, Pullback 0-8)
    Volume Layer (Volume + CVD):    0-30 pts  (Volume 0-12, CVD 0-12, RVOL bonus 0-6)

    Raw max = 100 pts
    Confluence bonus (2 timeframes confirmed) = x1.15 (capped at 100)

Usage:
    from signals.scorer import SignalScorer
    scorer = SignalScorer()
    result = scorer.score(ma_result, donchian_result, volume_result, cvd_result, rsi_result)
"""

from loguru import logger
import config


class SignalScorer:
    """
    Combines all indicator scores into a single confidence score.
    Applies confluence bonus and determines alert tier.
    """

    def score(
        self,
        ma_result:       dict,
        donchian_result: dict,
        volume_result:   dict,
        cvd_result:      dict,
        rsi_result:      dict,
        pullback_bonus:  int  = 0,
        rvol_bonus:      int  = 0,
        confluence:      bool = False,
    ) -> dict:
        """
        Calculate final confidence score.

        Args:
            ma_result:       Output from MovingAverages.analyze()
            donchian_result: Output from DonchianChannels.analyze()
            volume_result:   Output from VolumeAnalysis.analyze()
            cvd_result:      Output from CVDAnalysis.analyze()
            rsi_result:      Output from RSIAnalysis.analyze()
            pullback_bonus:  Extra pts if price pulled back cleanly to MA (0-8)
            rvol_bonus:      Extra pts for intraday RVOL spike (0-6)
            confluence:      True if same setup confirmed on 2 timeframes

        Returns:
            dict with:
                raw_score         — score before confluence bonus
                final_score       — score after confluence bonus (capped 100)
                tier              — "high_conviction", "standard", "watchlist", "none"
                direction         — "bullish" or "bearish"
                layer_scores      — breakdown by layer
                indicator_scores  — breakdown by indicator
                confluence_applied— bool
                alert_emoji       — 🔴 or 🟡 or None
        """

        # ── Layer 1: Trend (max 35) ──────────────────────────────
        trend_score = ma_result.get("score", 0)

        # ── Layer 2: Setup (max 35) ──────────────────────────────
        donchian_score  = donchian_result.get("score", 0)   # max 15
        rsi_score       = rsi_result.get("score", 0)         # max 12
        pullback_score  = min(pullback_bonus, 8)             # max 8
        setup_score     = donchian_score + rsi_score + pullback_score

        # ── Layer 3: Volume (max 30) ─────────────────────────────
        volume_score    = volume_result.get("score", 0)      # max 12
        cvd_score       = cvd_result.get("score", 0)         # max 12
        rvol_score      = min(rvol_bonus, 6)                 # max 6
        volume_layer    = volume_score + cvd_score + rvol_score

        # ── Raw total ────────────────────────────────────────────
        raw_score = trend_score + setup_score + volume_layer
        raw_score = min(raw_score, 100)

        # ── Confluence bonus ─────────────────────────────────────
        if confluence:
            final_score = min(round(raw_score * config.CONFLUENCE_BONUS_MULTIPLIER), 100)
            confluence_applied = True
        else:
            final_score = raw_score
            confluence_applied = False

        # ── Direction ────────────────────────────────────────────
        direction = self._determine_direction(ma_result, donchian_result, rsi_result)

        # ── Alert tier ───────────────────────────────────────────
        tier, emoji = self._determine_tier(final_score)

        result = {
            "raw_score":    raw_score,
            "final_score":  final_score,
            "tier":         tier,
            "direction":    direction,
            "alert_emoji":  emoji,
            "confluence_applied": confluence_applied,
            "layer_scores": {
                "trend":  {"score": trend_score,  "max": 35},
                "setup":  {"score": setup_score,  "max": 35},
                "volume": {"score": volume_layer, "max": 30},
            },
            "indicator_scores": {
                "moving_averages": {"score": trend_score,   "max": 35},
                "donchian":        {"score": donchian_score,"max": 15},
                "rsi":             {"score": rsi_score,     "max": 12},
                "pullback":        {"score": pullback_score,"max": 8},
                "volume":          {"score": volume_score,  "max": 12},
                "cvd":             {"score": cvd_score,     "max": 12},
                "rvol_bonus":      {"score": rvol_score,    "max": 6},
            },
        }

        logger.info(
            f"Score calculated — Raw: {raw_score} | "
            f"Final: {final_score} | Tier: {tier} | Direction: {direction}"
        )
        return result

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _determine_direction(
        self,
        ma_result:       dict,
        donchian_result: dict,
        rsi_result:      dict,
    ) -> str:
        """
        Determine overall trade direction from indicator consensus.
        Majority rules across the three setup signals.
        """
        bullish_votes = 0
        bearish_votes = 0

        # MA vote
        trend = ma_result.get("trend_direction", "neutral")
        if trend == "bullish":
            bullish_votes += 1
        elif trend == "bearish":
            bearish_votes += 1

        # Donchian vote
        if donchian_result.get("breakout_up") or donchian_result.get("near_upper"):
            bullish_votes += 1
        elif donchian_result.get("breakout_down") or donchian_result.get("near_lower"):
            bearish_votes += 1

        # RSI divergence vote
        if rsi_result.get("bullish_divergence"):
            bullish_votes += 1
        elif rsi_result.get("bearish_divergence"):
            bearish_votes += 1

        if bullish_votes > bearish_votes:
            return "bullish"
        elif bearish_votes > bullish_votes:
            return "bearish"
        else:
            return "neutral"

    @staticmethod
    def _determine_tier(score: int) -> tuple[str, str | None]:
        """Map score to alert tier and emoji."""
        if score >= config.SCORE_HIGH_CONVICTION:
            return "high_conviction", "🔴"
        elif score >= config.SCORE_ALERT_MINIMUM:
            return "standard", "🟡"
        elif score >= config.SCORE_WATCHLIST:
            return "watchlist", None
        else:
            return "none", None