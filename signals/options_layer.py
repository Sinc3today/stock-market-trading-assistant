"""
signals/options_layer.py — Options Execution Context
Takes a confirmed stock signal and recommends the appropriate options strategy.
Supports: single leg, debit spreads, credit spreads, iron condors.

Strategy selection logic:
  High conviction + Low IV   → Debit spread (primary) or single leg
  Standard + Low IV          → Debit spread
  Standard + High IV         → Credit spread (sell premium)
  Neutral signal + High IV   → Iron condor
  Any + Danger IV            → Blocked

Usage:
    from signals.options_layer import OptionsLayer
    ol = OptionsLayer()
    context = ol.analyze(ticker, score_result, stock_price, target, stop)
"""

import sys
import os
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

# ── IV thresholds ────────────────────────────────────────────
IV_RANK_LOW    = 30
IV_RANK_HIGH   = 50
IV_RANK_DANGER = 80

# ── DTE recommendations ──────────────────────────────────────
DTE_SWING_TARGET  = 45
DTE_SWING_MIN     = 21
DTE_INTRADAY_MIN  = 7
DTE_INTRADAY_MAX  = 14


class OptionsLayer:
    """
    Recommends options strategy based on stock signal + IV context.
    Primary focus: debit spreads. Full support for all spread types.
    """

    def analyze(
        self,
        ticker:       str,
        score_result: dict,
        stock_price:  float,
        target:       float,
        stop:         float,
        mode:         str   = "swing",
        iv_rank:      float = None,
        iv_current:   float = None,
    ) -> dict:
        """
        Generate full options strategy recommendation.

        Returns dict with strategy type, legs, max profit/loss,
        and Discord-ready summary block.
        """
        direction = score_result.get("direction", "neutral")
        score     = score_result.get("final_score", 0)

        if direction == "neutral" and (iv_rank is None or iv_rank < IV_RANK_HIGH):
            return self._no_trade("Direction neutral and IV not high enough for iron condor")

        if score < config.SCORE_ALERT_MINIMUM and direction != "neutral":
            return self._no_trade(f"Stock score too low ({score}) for options")

        # ── IV assessment ────────────────────────────────────────
        iv_assess = self._assess_iv(iv_rank)
        if not iv_assess["tradeable"]:
            return self._no_trade(iv_assess["reason"])

        # ── Strategy selection ───────────────────────────────────
        strategy = self._select_strategy(
            direction, score, iv_rank, iv_assess["label"]
        )

        # ── Build legs ───────────────────────────────────────────
        legs = self._build_legs(
            strategy, direction, stock_price, target, score, mode
        )

        # ── DTE ──────────────────────────────────────────────────
        dte_rec = self._recommend_dte(mode)

        # ── Max profit / max loss ────────────────────────────────
        risk_reward = self._calculate_risk_reward(strategy, legs)

        # ── Recommendation strength ──────────────────────────────
        rec, rec_emoji = self._build_recommendation(
            score, iv_assess["label"], strategy
        )

        result = {
            "ticker":         ticker,
            "tradeable":      True,
            "strategy":       strategy,
            "direction":      direction,
            "stock_score":    score,

            # IV
            "iv_rank":        iv_rank,
            "iv_assessment":  iv_assess["label"],
            "iv_note":        iv_assess["note"],

            # Legs
            "legs":           legs,
            "leg_count":      len(legs),

            # DTE
            "recommended_dte": dte_rec["dte"],
            "dte_note":        dte_rec["note"],

            # Risk / reward
            "net_premium":     risk_reward["net_premium"],
            "max_profit":      risk_reward["max_profit"],
            "max_loss":        risk_reward["max_loss"],
            "spread_rr":       risk_reward["rr_ratio"],

            # Summary
            "recommendation":  rec,
            "rec_emoji":       rec_emoji,
            "exit_rule":       self._exit_rule(strategy),

            # Discord
            "discord_addon":   self._format_discord_addon(
                strategy, legs, dte_rec,
                iv_assess, risk_reward,
                rec, rec_emoji
            ),
        }

        logger.info(
            f"Options context: {ticker} | Strategy: {strategy} | "
            f"IV Rank: {iv_rank} | Legs: {len(legs)} | {rec}"
        )
        return result

    # ─────────────────────────────────────────
    # STRATEGY SELECTION
    # ─────────────────────────────────────────

    def _select_strategy(
        self,
        direction:  str,
        score:      int,
        iv_rank:    float,
        iv_label:   str,
    ) -> str:
        """
        Select the appropriate strategy based on signal + IV.

        Priority order (debit spreads first per user preference):
          1. Debit spread  — directional, low-moderate IV, primary choice
          2. Single leg    — very high conviction only
          3. Credit spread — directional, high IV
          4. Iron condor   — neutral, high IV
        """
        iv = iv_rank or 0

        # Neutral signal → iron condor if IV is high enough
        if direction == "neutral" and iv >= IV_RANK_HIGH:
            return "iron_condor"

        # High conviction + low IV → debit spread (primary)
        if score >= config.SCORE_HIGH_CONVICTION and iv < IV_RANK_LOW:
            return "debit_spread"

        # High conviction + very low IV → single leg acceptable
        if score >= config.SCORE_HIGH_CONVICTION and iv < 20:
            return "single_leg"

        # Standard signal + low/moderate IV → debit spread (primary)
        if iv < IV_RANK_HIGH:
            return "debit_spread"

        # High IV → credit spread (sell the expensive premium)
        if iv >= IV_RANK_HIGH:
            return "credit_spread"

        return "debit_spread"  # Safe default

    # ─────────────────────────────────────────
    # LEG BUILDING
    # ─────────────────────────────────────────

    def _build_legs(
        self,
        strategy:    str,
        direction:   str,
        stock_price: float,
        target:      float,
        score:       int,
        mode:        str,
    ) -> list[dict]:
        """Build the individual legs for each strategy type."""

        if strategy == "single_leg":
            return self._single_leg(direction, stock_price)

        elif strategy == "debit_spread":
            return self._debit_spread_legs(direction, stock_price, target, score)

        elif strategy == "credit_spread":
            return self._credit_spread_legs(direction, stock_price)

        elif strategy == "iron_condor":
            return self._iron_condor_legs(stock_price)

        return []

    def _single_leg(self, direction: str, stock_price: float) -> list[dict]:
        opt_type = "CALL" if direction == "bullish" else "PUT"
        strike   = self._round_strike(stock_price)
        return [{
            "action":      "BUY",
            "option_type": opt_type,
            "strike":      strike,
            "note":        f"ATM {opt_type} @ ${strike}",
        }]

    def _debit_spread_legs(
        self,
        direction:   str,
        stock_price: float,
        target:      float,
        score:       int,
    ) -> list[dict]:
        """
        Bull Call Spread (bullish) or Bear Put Spread (bearish).
        Buy ATM/slight ITM + Sell near target.
        Spread width: ~$5 for most stocks, ~$10 for high-priced.
        """
        width = 10.0 if stock_price > 200 else 5.0

        if direction == "bullish":
            buy_strike  = self._round_strike(stock_price)           # ATM
            sell_strike = self._round_strike(stock_price + width)   # Near target
            return [
                {
                    "action":      "BUY",
                    "option_type": "CALL",
                    "strike":      buy_strike,
                    "note":        f"Buy ATM Call @ ${buy_strike}",
                },
                {
                    "action":      "SELL",
                    "option_type": "CALL",
                    "strike":      sell_strike,
                    "note":        f"Sell OTM Call @ ${sell_strike} (caps profit, reduces cost)",
                },
            ]
        else:
            buy_strike  = self._round_strike(stock_price)           # ATM
            sell_strike = self._round_strike(stock_price - width)   # Near target
            return [
                {
                    "action":      "BUY",
                    "option_type": "PUT",
                    "strike":      buy_strike,
                    "note":        f"Buy ATM Put @ ${buy_strike}",
                },
                {
                    "action":      "SELL",
                    "option_type": "PUT",
                    "strike":      sell_strike,
                    "note":        f"Sell OTM Put @ ${sell_strike} (caps profit, reduces cost)",
                },
            ]

    def _credit_spread_legs(
        self,
        direction:   str,
        stock_price: float,
    ) -> list[dict]:
        """
        Bull Put Spread (bullish) or Bear Call Spread (bearish).
        Sell closer to money + Buy further OTM for protection.
        """
        width = 5.0

        if direction == "bullish":
            # Sell put below price, buy put further below
            sell_strike = self._round_strike(stock_price * 0.97)
            buy_strike  = self._round_strike(stock_price * 0.97 - width)
            return [
                {
                    "action":      "SELL",
                    "option_type": "PUT",
                    "strike":      sell_strike,
                    "note":        f"Sell OTM Put @ ${sell_strike} (collect premium)",
                },
                {
                    "action":      "BUY",
                    "option_type": "PUT",
                    "strike":      buy_strike,
                    "note":        f"Buy further OTM Put @ ${buy_strike} (protection)",
                },
            ]
        else:
            sell_strike = self._round_strike(stock_price * 1.03)
            buy_strike  = self._round_strike(stock_price * 1.03 + width)
            return [
                {
                    "action":      "SELL",
                    "option_type": "CALL",
                    "strike":      sell_strike,
                    "note":        f"Sell OTM Call @ ${sell_strike} (collect premium)",
                },
                {
                    "action":      "BUY",
                    "option_type": "CALL",
                    "strike":      buy_strike,
                    "note":        f"Buy further OTM Call @ ${buy_strike} (protection)",
                },
            ]

    def _iron_condor_legs(self, stock_price: float) -> list[dict]:
        """
        Iron Condor: sell OTM put spread + sell OTM call spread.
        Profit zone: price stays between the two short strikes.
        Use when: neutral signal, high IV.
        """
        width       = 5.0
        put_short   = self._round_strike(stock_price * 0.96)
        put_long    = self._round_strike(put_short - width)
        call_short  = self._round_strike(stock_price * 1.04)
        call_long   = self._round_strike(call_short + width)

        return [
            {
                "action":      "SELL",
                "option_type": "PUT",
                "strike":      put_short,
                "note":        f"Sell OTM Put @ ${put_short}",
            },
            {
                "action":      "BUY",
                "option_type": "PUT",
                "strike":      put_long,
                "note":        f"Buy lower Put @ ${put_long} (protection)",
            },
            {
                "action":      "SELL",
                "option_type": "CALL",
                "strike":      call_short,
                "note":        f"Sell OTM Call @ ${call_short}",
            },
            {
                "action":      "BUY",
                "option_type": "CALL",
                "strike":      call_long,
                "note":        f"Buy higher Call @ ${call_long} (protection)",
            },
        ]

    # ─────────────────────────────────────────
    # RISK / REWARD
    # ─────────────────────────────────────────

    def _calculate_risk_reward(
        self, strategy: str, legs: list[dict]
    ) -> dict:
        """
        Calculate max profit, max loss, and R/R for each strategy.
        Uses estimated premiums (actual values come from your broker).
        """
        if not legs:
            return {"net_premium": 0, "max_profit": 0,
                    "max_loss": 0, "rr_ratio": 0}

        strikes = [l["strike"] for l in legs if l.get("strike")]

        if strategy == "single_leg":
            est_premium = round(legs[0]["strike"] * 0.03, 2)
            return {
                "net_premium": f"${est_premium} debit (estimated)",
                "max_profit":  "Unlimited (sell when target hit)",
                "max_loss":    f"${round(est_premium * 100, 2)} per contract",
                "rr_ratio":    "Defined by exit",
            }

        elif strategy == "debit_spread":
            if len(strikes) >= 2:
                width     = round(abs(strikes[1] - strikes[0]), 2)
                est_debit = round(width * 0.40, 2)   # ~40% of width
                max_profit = round((width - est_debit) * 100, 2)
                max_loss   = round(est_debit * 100, 2)
                rr         = round(max_profit / max_loss, 2) if max_loss > 0 else 0
                return {
                    "net_premium": f"~${est_debit} debit per share (${max_loss}/contract est.)",
                    "max_profit":  f"~${max_profit} per contract (at expiry, both legs ITM)",
                    "max_loss":    f"~${max_loss} per contract (premium paid)",
                    "rr_ratio":    f"{rr}:1 (estimated)",
                }

        elif strategy == "credit_spread":
            if len(strikes) >= 2:
                width      = round(abs(strikes[0] - strikes[1]), 2)
                est_credit = round(width * 0.35, 2)
                max_profit = round(est_credit * 100, 2)
                max_loss   = round((width - est_credit) * 100, 2)
                rr         = round(max_profit / max_loss, 2) if max_loss > 0 else 0
                return {
                    "net_premium": f"~${est_credit} credit per share (${max_profit}/contract est.)",
                    "max_profit":  f"~${max_profit} per contract (keep full credit)",
                    "max_loss":    f"~${max_loss} per contract (spread width - credit)",
                    "rr_ratio":    f"{rr}:1 (estimated)",
                }

        elif strategy == "iron_condor":
            put_strikes  = [l["strike"] for l in legs if l["option_type"] == "PUT"]
            call_strikes = [l["strike"] for l in legs if l["option_type"] == "CALL"]
            if len(put_strikes) >= 2 and len(call_strikes) >= 2:
                width      = round(abs(put_strikes[0] - put_strikes[1]), 2)
                est_credit = round(width * 0.30 * 2, 2)  # Both sides
                max_profit = round(est_credit * 100, 2)
                max_loss   = round((width - est_credit / 2) * 100, 2)
                return {
                    "net_premium": f"~${est_credit} total credit (estimated)",
                    "max_profit":  f"~${max_profit} per condor (price stays in range)",
                    "max_loss":    f"~${max_loss} per condor (one side breaks out)",
                    "rr_ratio":    f"{round(max_profit/max_loss,2)}:1 (estimated)",
                }

        return {"net_premium": "See broker", "max_profit": "See broker",
                "max_loss": "See broker", "rr_ratio": "N/A"}

    # ─────────────────────────────────────────
    # IV ASSESSMENT
    # ─────────────────────────────────────────

    def _assess_iv(self, iv_rank: float) -> dict:
        if iv_rank is None:
            return {
                "tradeable": True,
                "label":     "Unknown",
                "note":      "⚠️ IV Rank unavailable — verify manually before trading",
            }
        if iv_rank >= IV_RANK_DANGER:
            return {
                "tradeable": False,
                "label":     "Danger",
                "reason":    f"IV Rank {iv_rank} — extreme, high crush risk",
                "note":      f"IV Rank {iv_rank}/100 — avoid buying premium here",
            }
        elif iv_rank >= IV_RANK_HIGH:
            return {
                "tradeable": True,
                "label":     "High",
                "note":      f"IV Rank {iv_rank}/100 — sell premium (credit spread / condor)",
            }
        elif iv_rank >= IV_RANK_LOW:
            return {
                "tradeable": True,
                "label":     "Moderate",
                "note":      f"IV Rank {iv_rank}/100 — debit spread is solid here",
            }
        else:
            return {
                "tradeable": True,
                "label":     "Low",
                "note":      f"IV Rank {iv_rank}/100 — cheap premium ✅ debit spread ideal",
            }

    # ─────────────────────────────────────────
    # DTE / RECOMMENDATION / EXIT
    # ─────────────────────────────────────────

    def _recommend_dte(self, mode: str) -> dict:
        if mode == "swing":
            return {
                "dte":  DTE_SWING_TARGET,
                "note": f"{DTE_SWING_TARGET} DTE — gives the trade room to work. "
                        f"Never go below {DTE_SWING_MIN} DTE on spreads.",
            }
        return {
            "dte":  DTE_INTRADAY_MIN,
            "note": f"{DTE_INTRADAY_MIN} DTE — short-dated momentum play. "
                    f"Never use 0DTE on spreads.",
        }

    def _exit_rule(self, strategy: str) -> str:
        rules = {
            "debit_spread":  "Close at 50-75% of max profit OR at 50% loss of debit paid",
            "credit_spread": "Close at 50% of max profit (buy back for 50% of credit received)",
            "iron_condor":   "Close at 50% profit OR if one short strike is breached",
            "single_leg":    "Fixed % target — no trailing stops on options",
        }
        return rules.get(strategy, "Close at 50% max profit")

    def _build_recommendation(
        self, score: int, iv_label: str, strategy: str
    ) -> tuple[str, str]:
        strategy_label = strategy.replace("_", " ").title()
        if score >= config.SCORE_HIGH_CONVICTION and iv_label in ("Low", "Moderate", "Unknown"):
            return f"Strong setup — {strategy_label} recommended", "🟢"
        elif score >= config.SCORE_ALERT_MINIMUM and iv_label in ("Low", "Moderate"):
            return f"Good setup — {strategy_label} fits well", "🟢"
        elif iv_label == "High":
            return f"High IV — {strategy_label} to capture premium", "🟡"
        else:
            return f"{strategy_label} — verify IV before entering", "🟡"

    # ─────────────────────────────────────────
    # DISCORD ADDON
    # ─────────────────────────────────────────

    def _format_discord_addon(
        self,
        strategy:    str,
        legs:        list,
        dte_rec:     dict,
        iv_assess:   dict,
        risk_reward: dict,
        rec:         str,
        rec_emoji:   str,
    ) -> str:
        strategy_label = strategy.replace("_", " ").upper()
        legs_str = "\n".join(
            f"    {l['action']} {l['option_type']} ${l['strike']} — {l['note']}"
            for l in legs
        )
        return (
            f"\n📋 **OPTIONS — {strategy_label}**\n"
            f"  Legs:\n{legs_str}\n"
            f"  DTE:        {dte_rec['dte']} days\n"
            f"  IV Rank:    {iv_assess['label']} — {iv_assess['note']}\n"
            f"  Net Cost:   {risk_reward['net_premium']}\n"
            f"  Max Profit: {risk_reward['max_profit']}\n"
            f"  Max Loss:   {risk_reward['max_loss']}\n"
            f"  R/R:        {risk_reward['rr_ratio']}\n"
            f"  Exit Rule:  {self._exit_rule(strategy)}\n"
            f"  {rec_emoji} {rec}\n"
        )

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    @staticmethod
    def _round_strike(price: float) -> float:
        return round(round(price / 1) * 1, 2)

    @staticmethod
    def _no_trade(reason: str) -> dict:
        return {
            "tradeable":      False,
            "reason":         reason,
            "strategy":       "none",
            "legs":           [],
            "recommendation": reason,
            "rec_emoji":      "🔴",
            "discord_addon":  f"\n📋 **OPTIONS:** Not recommended — {reason}\n",
        }