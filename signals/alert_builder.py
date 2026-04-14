"""
signals/alert_builder.py — Alert Object Builder
Packages score result + gate data into a clean alert object.
This is what gets sent to Discord and displayed on the Dashboard.

Usage:
    from signals.alert_builder import AlertBuilder
    builder = AlertBuilder()
    alert = builder.build(ticker, timeframe, mode, score_result, gate_data, indicator_results)
"""

from datetime import datetime
import pytz
from loguru import logger


class AlertBuilder:
    """
    Assembles all signal data into a standardized alert object.
    The alert object is the single source of truth passed to all delivery systems.
    """

    def build(
        self,
        ticker:             str,
        timeframe:          str,
        mode:               str,
        score_result:       dict,
        gate_data:          dict,
        ma_result:          dict,
        donchian_result:    dict,
        volume_result:      dict,
        cvd_result:         dict,
        rsi_result:         dict,
        entry:              float,
        stop:               float,
        target:             float,
        exit_type:          str,
        confluence_timeframes: list[str] = None,
    ) -> dict:
        """
        Build a complete alert object.

        Args:
            ticker:       Stock symbol e.g. "AAPL"
            timeframe:    Primary timeframe e.g. "day", "15min"
            mode:         "swing" or "intraday"
            score_result: Output from SignalScorer.score()
            gate_data:    Output from AlertGates.check()
            ma/don/vol/cvd/rsi results: All indicator outputs
            entry:        Entry price zone midpoint
            stop:         Stop loss price
            target:       Profit target price
            exit_type:    "trail_stop", "structure", "fixed_pct"
            confluence_timeframes: list of timeframes that confirmed e.g. ["day", "4hour"]

        Returns:
            Complete alert dict ready for Discord + Dashboard
        """

        now_est = datetime.now(pytz.timezone("US/Eastern"))
        timestamp = now_est.strftime("%Y-%m-%d %I:%M %p EST")

        direction  = score_result.get("direction", "neutral")
        score      = score_result.get("final_score", 0)
        tier       = score_result.get("tier", "none")
        emoji      = score_result.get("alert_emoji", "")
        rr_ratio   = gate_data.get("rr_ratio", 0)
        earnings   = gate_data.get("earnings_date", None)

        # Build setup description from indicator results
        setup_tags = self._build_setup_tags(donchian_result, rsi_result, ma_result)

        # Exit type label
        exit_label = {
            "trail_stop": "Trail Stop (MA-based)",
            "structure":  "Chart Structure",
            "fixed_pct":  "Fixed % Target",
        }.get(exit_type, exit_type)

        alert = {
            # Identity
            "ticker":       ticker,
            "timestamp":    timestamp,
            "mode":         mode.capitalize(),
            "timeframe":    timeframe,
            "direction":    direction.upper(),
            "tier":         tier,
            "emoji":        emoji,

            # Score
            "final_score":  score,
            "raw_score":    score_result.get("raw_score", 0),
            "confluence":   score_result.get("confluence_applied", False),
            "confluence_timeframes": confluence_timeframes or [timeframe],

            # Layer scores for display
            "layer_scores": score_result.get("layer_scores", {}),
            "indicator_scores": score_result.get("indicator_scores", {}),

            # Trade levels
            "entry":        round(entry,  2),
            "stop":         round(stop,   2),
            "target":       round(target, 2),
            "rr_ratio":     round(rr_ratio, 2),
            "exit_type":    exit_label,

            # Indicator snapshots
            "ma20":         ma_result.get("ma20"),
            "ma50":         ma_result.get("ma50"),
            "ma200":        ma_result.get("ma200"),
            "rsi":          rsi_result.get("rsi_current"),
            "rvol":         volume_result.get("rvol"),
            "cvd_slope":    cvd_result.get("cvd_slope"),

            # Setup description
            "setup_tags":   setup_tags,

            # Metadata
            "earnings_date": earnings,
            "instrument":   "stock",  # "options" in Phase 2
        }

        logger.info(
            f"Alert built — {ticker} {emoji} {tier.upper()} | "
            f"Score: {score} | Dir: {direction} | R/R: {rr_ratio:.2f}:1"
        )
        return alert

    def format_discord_message(self, alert: dict) -> str:
        """
        Format alert as a Discord message string.
        This is what gets posted to the Discord channel.
        """
        emoji      = alert["emoji"]
        tier_label = "HIGH CONVICTION ALERT" if alert["tier"] == "high_conviction" \
                     else "STANDARD ALERT"

        direction_emoji = "📈" if alert["direction"] == "BULLISH" else "📉"

        # Score breakdown lines
        layers = alert.get("layer_scores", {})
        trend_s  = layers.get("trend",  {})
        setup_s  = layers.get("setup",  {})
        volume_s = layers.get("volume", {})

        # Timeframe line
        tf_list = alert.get("confluence_timeframes", [alert["timeframe"]])
        tf_str  = " + ".join(tf_list) if len(tf_list) > 1 else tf_list[0]
        tf_note = f"⏱ Confirmed on: {tf_str}" if len(tf_list) > 1 \
                  else f"⏱ Timeframe: {tf_str}"

        # Setup tags
        tags = "  ".join(alert.get("setup_tags", [])) or "—"

        msg = (
            f"{emoji} **{tier_label}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Ticker:**     {alert['ticker']}\n"
            f"**Mode:**       {alert['mode']} Trade\n"
            f"**Direction:**  {alert['direction']} {direction_emoji}\n"
            f"**Score:**      {alert['final_score']} / 100\n"
            f"⏰ **Found:**   {alert['timestamp']}\n"
            f"\n"
            f"📊 **Score Breakdown**\n"
            f"  Trend:   {trend_s.get('score', 0)}/{trend_s.get('max', 35)}\n"
            f"  Setup:   {setup_s.get('score', 0)}/{setup_s.get('max', 35)}\n"
            f"  Volume:  {volume_s.get('score', 0)}/{volume_s.get('max', 30)}\n"
            f"\n"
            f"🔍 **Setup:**   {tags}\n"
            f"\n"
            f"📍 **Trade Levels**\n"
            f"  Entry:   ${alert['entry']}\n"
            f"  Stop:    ${alert['stop']}\n"
            f"  Target:  ${alert['target']}\n"
            f"  R/R:     {alert['rr_ratio']} : 1\n"
            f"  Exit:    {alert['exit_type']}\n"
            f"\n"
            f"📈 **Indicators**\n"
            f"  RSI: {alert['rsi']}  |  RVOL: {alert['rvol']}x  |  CVD: {alert['cvd_slope']}\n"
            f"  MA20: ${alert['ma20']}  MA50: ${alert['ma50']}  MA200: ${alert['ma200']}\n"
            f"\n"
            f"{tf_note}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return msg

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _build_setup_tags(
        self,
        donchian_result: dict,
        rsi_result:      dict,
        ma_result:       dict,
    ) -> list[str]:
        """Build short human-readable tags describing what triggered the setup."""
        tags = []

        if donchian_result.get("breakout_up"):
            tags.append("✅ Donchian breakout UP")
        elif donchian_result.get("breakout_down"):
            tags.append("✅ Donchian breakout DOWN")
        elif donchian_result.get("near_upper"):
            tags.append("⚠️ Testing upper channel")
        elif donchian_result.get("near_lower"):
            tags.append("⚠️ Testing lower channel")

        if rsi_result.get("bullish_divergence"):
            strength = rsi_result.get("divergence_strength", "")
            tags.append(f"✅ RSI bullish divergence ({strength})")
        elif rsi_result.get("bearish_divergence"):
            strength = rsi_result.get("divergence_strength", "")
            tags.append(f"✅ RSI bearish divergence ({strength})")

        if ma_result.get("stack_bullish"):
            tags.append("✅ MA stack bullish")
        elif ma_result.get("stack_bearish"):
            tags.append("✅ MA stack bearish")

        if ma_result.get("higher_highs_lows"):
            tags.append("✅ HH/HL structure")
        elif ma_result.get("lower_highs_lows"):
            tags.append("✅ LH/LL structure")

        return tags