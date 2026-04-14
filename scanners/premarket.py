"""
scanners/premarket.py — Premarket Scanner
Runs at 8:00 AM EST — 90 min before market open.
Scans entire watchlist for gap setups and premarket volume.
Fires PREPARATION alerts (not full trade alerts).
Feeds priority context into the swing scanner at 9:00 AM.

Preparation alert fires when:
  • Gap >= 1% in either direction
  • Premarket volume >= 0.5x average daily volume
  • Gap direction aligns with trend (MA stack)

Usage:
    from scanners.premarket import PremarketScanner
    scanner = PremarketScanner()
    scanner.run()
"""

import json
import sys
import os
from datetime import datetime, timedelta
from loguru import logger
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from data.polygon_client import PolygonClient
from indicators.moving_averages import MovingAverages


# ── Thresholds ───────────────────────────────────────────────
GAP_MINIMUM_PCT        = 1.0   # Minimum gap % to flag
PREMARKET_VOL_MIN      = 0.5   # Premarket vol must be >= 0.5x avg daily vol
GAP_STRONG_PCT         = 3.0   # Gap >= 3% = strong gap flag
PREMARKET_VOL_STRONG   = 1.5   # Vol >= 1.5x avg = strong volume flag


class PremarketScanner:
    """
    Scans for premarket gap setups across the full watchlist.
    Outputs prioritized preparation alerts before market open.
    """

    def __init__(self):
        self.client  = PolygonClient()
        self.eastern = pytz.timezone("US/Eastern")
        self.discord_post_fn = None
        self._priority_list  = []  # Shared with swing scanner

    def set_discord_fn(self, fn):
        self.discord_post_fn = fn

    def get_priority_list(self) -> list[str]:
        """
        Return tickers flagged by premarket scan.
        Called by swing scanner at 9:00 AM to prioritize scoring.
        """
        return self._priority_list

    def run(self) -> list[dict]:
        """
        Run the full premarket scan.
        Returns list of preparation alerts.
        """
        watchlist = self._load_watchlist()

        # Scan both swing and intraday watchlists premarket
        all_tickers = list(set(
            watchlist.get("swing", []) +
            watchlist.get("intraday", [])
        ))

        if not all_tickers:
            logger.warning("Watchlist empty — premarket scan skipped")
            return []

        now_str = datetime.now(self.eastern).strftime("%I:%M %p EST")
        logger.info(f"🌅 Premarket scan starting at {now_str} — {len(all_tickers)} tickers")

        results      = []
        priority     = []

        for ticker in all_tickers:
            try:
                result = self._scan_ticker(ticker)
                if result:
                    results.append(result)
                    if result.get("priority"):
                        priority.append(ticker)
            except Exception as e:
                logger.error(f"Premarket scan error for {ticker}: {e}")
                continue

        # Sort by gap strength
        results.sort(key=lambda x: abs(x.get("gap_pct", 0)), reverse=True)

        # Store priority list for swing scanner
        self._priority_list = priority

        # Post summary to Discord
        if results and self.discord_post_fn:
            summary = self._build_summary_message(results)
            self._post_summary(summary)

        logger.info(
            f"🌅 Premarket scan complete — "
            f"{len(results)} setups found | "
            f"{len(priority)} priority tickers: {priority}"
        )
        return results

    # ─────────────────────────────────────────
    # CORE SCAN LOGIC
    # ─────────────────────────────────────────

    def _scan_ticker(self, ticker: str) -> dict | None:
        """
        Analyze premarket gap and volume for a single ticker.
        Returns result dict if gap meets minimum threshold, None otherwise.
        """
        # ── Fetch recent daily bars ──────────────────────────────
        df = self.client.get_bars(
            ticker, timeframe="day", limit=30, days_back=45
        )
        if df is None or len(df) < 5:
            logger.warning(f"No daily data for {ticker} — skipping premarket")
            return None

        prev_close = float(df["close"].iloc[-1])
        avg_volume = float(df["volume"].tail(20).mean())

        # ── Fetch premarket bar (most recent) ────────────────────
        premarket_price, premarket_volume = self._get_premarket_data(ticker, prev_close)

        if premarket_price is None:
            logger.debug(f"No premarket data for {ticker}")
            return None

        # ── Calculate gap ────────────────────────────────────────
        gap_pct = round(((premarket_price - prev_close) / prev_close) * 100, 2)
        gap_dir = "up" if gap_pct > 0 else "down"

        if abs(gap_pct) < GAP_MINIMUM_PCT:
            logger.debug(f"{ticker} gap {gap_pct}% below minimum — skipping")
            return None

        # ── Volume check ─────────────────────────────────────────
        vol_ratio = round(premarket_volume / avg_volume, 2) if avg_volume > 0 else 0
        if vol_ratio < PREMARKET_VOL_MIN:
            logger.debug(f"{ticker} premarket volume too low ({vol_ratio}x) — skipping")
            return None

        # ── Trend alignment check ────────────────────────────────
        ma_result = MovingAverages(df).analyze()
        trend     = ma_result.get("trend_direction", "neutral")

        gap_with_trend = (
            (gap_dir == "up"   and trend == "bullish") or
            (gap_dir == "down" and trend == "bearish")
        )
        gap_against_trend = (
            (gap_dir == "up"   and trend == "bearish") or
            (gap_dir == "down" and trend == "bullish")
        )

        # ── Determine strength and priority ──────────────────────
        strong_gap = abs(gap_pct)  >= GAP_STRONG_PCT
        strong_vol = vol_ratio     >= PREMARKET_VOL_STRONG

        # Priority = strong gap + volume + trend aligned
        priority = gap_with_trend and (strong_gap or strong_vol)

        # ── Build preparation alert ──────────────────────────────
        now_est   = datetime.now(self.eastern).strftime("%Y-%m-%d %I:%M %p EST")
        gap_emoji = "⬆️" if gap_dir == "up" else "⬇️"
        trend_emoji = {
            "bullish": "📈", "bearish": "📉", "neutral": "➡️"
        }.get(trend, "➡️")

        result = {
            "ticker":          ticker,
            "timestamp":       now_est,
            "type":            "premarket",
            "prev_close":      round(prev_close, 2),
            "premarket_price": round(premarket_price, 2),
            "gap_pct":         gap_pct,
            "gap_direction":   gap_dir,
            "gap_emoji":       gap_emoji,
            "premarket_volume":int(premarket_volume),
            "avg_daily_volume":int(avg_volume),
            "vol_ratio":       vol_ratio,
            "trend":           trend,
            "trend_emoji":     trend_emoji,
            "gap_with_trend":  gap_with_trend,
            "gap_against_trend": gap_against_trend,
            "strong_gap":      strong_gap,
            "strong_vol":      strong_vol,
            "priority":        priority,
            "ma20":            ma_result.get("ma20"),
            "ma50":            ma_result.get("ma50"),
            "ma200":           ma_result.get("ma200"),
        }

        strength = "🔥 STRONG" if (strong_gap and strong_vol) else \
                   "⚡ MODERATE" if (strong_gap or strong_vol) else "👀 WATCH"

        logger.info(
            f"🌅 {ticker} {gap_emoji} {gap_pct:+.1f}% | "
            f"Vol: {vol_ratio:.1f}x | Trend: {trend} | "
            f"{'✅ WITH trend' if gap_with_trend else '⚠️ AGAINST trend'} | "
            f"{strength}"
        )
        return result

    # ─────────────────────────────────────────
    # PREMARKET DATA
    # ─────────────────────────────────────────

    def _get_premarket_data(
        self, ticker: str, prev_close: float
    ) -> tuple[float | None, float]:
        """
        Get premarket price and volume.
        Uses the most recent 1-min bar from premarket session (4AM-9:30AM EST).
        Falls back to previous close if no premarket data available.
        """
        try:
            df_1min = self.client.get_bars(
                ticker,
                timeframe="1min",
                limit=100,
                days_back=1,
            )

            if df_1min is None or df_1min.empty:
                return None, 0

            # Filter to premarket hours (4:00 AM - 9:30 AM EST)
            df_1min.index = df_1min.index.tz_localize("UTC").tz_convert("US/Eastern") \
                if df_1min.index.tz is None else df_1min.index.tz_convert("US/Eastern")

            premarket = df_1min.between_time("04:00", "09:29")

            if premarket.empty:
                return None, 0

            premarket_price  = float(premarket["close"].iloc[-1])
            premarket_volume = float(premarket["volume"].sum())

            return premarket_price, premarket_volume

        except Exception as e:
            logger.debug(f"Premarket data fetch failed for {ticker}: {e}")
            return None, 0

    # ─────────────────────────────────────────
    # DISCORD SUMMARY
    # ─────────────────────────────────────────

    def _build_summary_message(self, results: list[dict]) -> str:
        """
        Build a premarket summary card for Discord.
        Shows all flagged tickers ranked by gap size.
        """
        now_est = datetime.now(self.eastern).strftime("%Y-%m-%d %I:%M %p EST")
        priority = [r for r in results if r.get("priority")]
        watch    = [r for r in results if not r.get("priority")]

        lines = [
            f"🌅 **PREMARKET SCAN** — {now_est}",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if priority:
            lines.append(f"\n🔥 **PRIORITY SETUPS** ({len(priority)})")
            for r in priority:
                trend_note = "✅ WITH trend" if r["gap_with_trend"] else "⚠️ AGAINST trend"
                lines.append(
                    f"  {r['gap_emoji']} **{r['ticker']}** "
                    f"{r['gap_pct']:+.1f}% | "
                    f"Vol: {r['vol_ratio']:.1f}x | "
                    f"{r['trend_emoji']} {r['trend'].upper()} | "
                    f"{trend_note}"
                )

        if watch:
            lines.append(f"\n👀 **WATCH LIST** ({len(watch)})")
            for r in watch:
                lines.append(
                    f"  {r['gap_emoji']} **{r['ticker']}** "
                    f"{r['gap_pct']:+.1f}% | "
                    f"Vol: {r['vol_ratio']:.1f}x | "
                    f"{r['trend_emoji']} {r['trend'].upper()}"
                )

        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"⏰ Swing scan fires at 9:00 AM EST")

        return "\n".join(lines)

    def _post_summary(self, message: str):
        """Post premarket summary to standard alerts channel."""
        try:
            from alerts.discord_bot import bot
            import asyncio

            channel_id = config.DISCORD_CHANNEL_ID_STANDARD
            channel    = bot.get_channel(channel_id)

            if channel:
                if bot.loop and bot.loop.is_running():
                    import asyncio
                    asyncio.run_coroutine_threadsafe(
                        channel.send(message), bot.loop
                    )
        except Exception as e:
            logger.error(f"Failed to post premarket summary: {e}")

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _load_watchlist(self) -> dict:
        try:
            with open(config.WATCHLIST_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load watchlist: {e}")
            return {"swing": [], "intraday": []}