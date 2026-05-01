"""
scanners/swing_scanner.py — Swing Trade Scanner (v2 — SPY Options Focus)

Changes from v1:
  - SPYOptionsEngine is wired in for SPY-specific call/put spread + IC analysis
  - Score thresholds from config now actually fire (45/68 vs old 75/90)
  - Discord message includes full options legs when SPY is the ticker
  - Watchlist expanded via config/watchlist.json

Run standalone:
    python -m scanners.swing_scanner
"""

import json
import sys
import os
from datetime import datetime
from loguru import logger
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from data.polygon_client import PolygonClient
from data.cache import cache_get, cache_set
from indicators.moving_averages import MovingAverages
from indicators.donchian import DonchianChannels
from indicators.volume import VolumeAnalysis
from indicators.cvd import CVDAnalysis
from indicators.rsi import RSIAnalysis
from signals.scorer import SignalScorer
from signals.gates import AlertGates
from signals.alert_builder import AlertBuilder
from signals.spy_options_engine import SPYOptionsEngine   # ← NEW
from journal.trade_logger import TradeLogger


class SwingScanner:
    """Swing trade scanner — runs daily at 9:00 AM ET on weekdays."""

    def __init__(self):
        self.client          = PolygonClient()
        self.scorer          = SignalScorer()
        self.gates           = AlertGates()
        self.builder         = AlertBuilder()
        self.spy_engine      = SPYOptionsEngine()         # ← NEW
        self.logger          = TradeLogger()
        self.discord_post_fn = None
        self.premarket_scanner = None  # Injected by main.py

    def set_discord_fn(self, fn):
        """Register the Discord posting callable."""
        self.discord_post_fn = fn

    def run(self) -> list[dict]:
        """Scan watchlist for swing setups; skips weekends automatically."""
        now_et = datetime.now(pytz.timezone("US/Eastern"))
        if now_et.weekday() >= 5:
            logger.info("Weekend -- swing scan skipped")
            return []

        watchlist = self._load_watchlist()
        tickers   = watchlist.get("swing", [])

        if not tickers:
            logger.warning("Swing watchlist is empty")
            return []

        logger.info(f"Starting swing scan — {len(tickers)} tickers: {tickers}")
        fired_alerts = []

        # ── SPY gets full options engine first ───────────────────
        if "SPY" in tickers:
            spy_alerts = self._scan_spy_options()
            fired_alerts.extend(spy_alerts)

        # ── All other tickers go through standard scorer ─────────
        for ticker in tickers:
            try:
                alert = self._scan_ticker(ticker)
                if alert:
                    fired_alerts.append(alert)
            except Exception as e:
                logger.error(f"Error scanning {ticker}: {e}")
                continue

        logger.info(
            f"Swing scan complete — "
            f"{len(fired_alerts)} alerts fired from {len(tickers)} tickers"
        )
        return fired_alerts

    # ─────────────────────────────────────────
    # SPY OPTIONS ENGINE
    # ─────────────────────────────────────────

    def _scan_spy_options(self) -> list[dict]:
        """
        Run SPY through the dedicated options engine.
        Returns list of alert dicts for Discord posting.
        """
        logger.info("Running SPY options engine...")

        df_daily = self._fetch("SPY", config.SWING_PRIMARY_TIMEFRAME, 300, 400)
        if df_daily is None:
            logger.warning("SPY: no daily data")
            return []

        df_intraday = self._fetch("SPY", config.INTRADAY_PRIMARY_TIMEFRAME, 100, 5)

        setups = self.spy_engine.analyze(df_daily, df_intraday)
        alerts = []

        for setup in setups:
            if setup.conviction not in ("high", "standard"):
                continue

            # Build a compatible alert dict for the existing Discord/logger system
            tier = "high_conviction" if setup.conviction == "high" else "standard"
            emoji = "🔴" if tier == "high_conviction" else "🟡"

            now_est = datetime.now().strftime("%Y-%m-%d %I:%M %p EST")
            alert = {
                "ticker":      "SPY",
                "timestamp":   now_est,
                "mode":        "Swing",
                "timeframe":   "day",
                "direction":   setup.direction.upper() if setup.direction else "NEUTRAL",
                "tier":        tier,
                "emoji":       emoji,
                "final_score": setup.score,
                "strategy":    setup.strategy,
                "setup_tags":  setup.reasons[:4],
                # Pass-through for Discord formatting
                "_spy_setup":  setup,
            }

            # Log to journal
            self.logger.log_alert(alert)

            # Format and post to Discord
            discord_msg = setup.to_discord_msg()
            if self.discord_post_fn:
                self.discord_post_fn(alert, discord_msg)

            logger.info(
                f"🔔 SPY OPTIONS ALERT — {setup.strategy.upper()} | "
                f"{setup.conviction.upper()} | Score: {setup.score}"
            )
            alerts.append(alert)

        if not setups:
            logger.info("SPY options engine: no qualifying setups today")

        return alerts

    # ─────────────────────────────────────────
    # STANDARD TICKER SCAN (non-SPY)
    # ─────────────────────────────────────────

    def _scan_ticker(self, ticker: str) -> dict | None:
        """Standard score-based scan for non-SPY tickers."""
        if ticker == "SPY":
            return None  # SPY handled separately above

        logger.info(f"Scanning {ticker}...")

        df_primary   = self._fetch(ticker, config.SWING_PRIMARY_TIMEFRAME,   300, 400)
        df_secondary = self._fetch(ticker, config.SWING_SECONDARY_TIMEFRAME, 300, 100)

        if df_primary is None or len(df_primary) < 50:
            logger.warning(f"Insufficient data for {ticker}")
            return None

        ma_r   = MovingAverages(df_primary).analyze()
        dc_r   = DonchianChannels(df_primary).analyze()
        vol_r  = VolumeAnalysis(df_primary).analyze()
        cvd_r  = CVDAnalysis(df_primary).analyze()
        rsi_r  = RSIAnalysis(df_primary).analyze()

        confluence     = False
        confluence_tfs = [config.SWING_PRIMARY_TIMEFRAME]

        if df_secondary is not None and len(df_secondary) >= 50:
            ma_r2 = MovingAverages(df_secondary).analyze()
            if ma_r2.get("trend_direction") == ma_r.get("trend_direction") \
               and ma_r.get("trend_direction") != "neutral":
                confluence     = True
                confluence_tfs = [config.SWING_PRIMARY_TIMEFRAME, config.SWING_SECONDARY_TIMEFRAME]
                logger.info(f"{ticker} confluence confirmed")

        score_result = self.scorer.score(
            ma_r, dc_r, vol_r, cvd_r, rsi_r, confluence=confluence
        )

        score = score_result["final_score"]
        tier  = score_result["tier"]

        if tier == "watchlist":
            watchlist_alert = self.builder.build(
                ticker=ticker, timeframe=config.SWING_PRIMARY_TIMEFRAME,
                mode="swing", score_result=score_result, gate_data={},
                ma_result=ma_r, donchian_result=dc_r, volume_result=vol_r,
                cvd_result=cvd_r, rsi_result=rsi_r,
                entry=0, stop=0, target=0, exit_type="tbd",
                confluence_timeframes=confluence_tfs,
            )
            self.logger.log_watchlist_entry(watchlist_alert)
            logger.info(f"{ticker} → Watchlist ({score}/100)")
            return None

        if tier == "none":
            logger.info(f"{ticker} → No signal ({score}/100)")
            return None

        entry, stop, target, exit_type = self._calculate_levels(
            df_primary, ma_r, dc_r, score_result["direction"]
        )
        if entry is None:
            return None

        passed, failures, gate_data = self.gates.check(
            score_result, ticker, entry, stop, target
        )
        if not passed:
            logger.info(f"{ticker} → Gates failed: {failures}")
            return None

        alert   = self.builder.build(
            ticker=ticker, timeframe=config.SWING_PRIMARY_TIMEFRAME,
            mode="swing", score_result=score_result, gate_data=gate_data,
            ma_result=ma_r, donchian_result=dc_r, volume_result=vol_r,
            cvd_result=cvd_r, rsi_result=rsi_r,
            entry=entry, stop=stop, target=target, exit_type=exit_type,
            confluence_timeframes=confluence_tfs,
        )
        message = self.builder.format_discord_message(alert)
        self.logger.log_alert(alert)

        if self.discord_post_fn:
            self.discord_post_fn(alert, message)

        logger.info(
            f"🔔 ALERT — {ticker} | Score: {score} | "
            f"{score_result['direction'].upper()} | R/R: {gate_data.get('rr_ratio',0):.2f}:1"
        )
        return alert

    # ─────────────────────────────────────────
    # DATA FETCHING
    # ─────────────────────────────────────────

    def _fetch(
        self, ticker: str, timeframe: str, limit: int, days_back: int
    ):
        key = f"{ticker}_{timeframe}"
        df  = cache_get(key)
        if df is None:
            df = self.client.get_bars(ticker, timeframe=timeframe,
                                      limit=limit, days_back=days_back)
            if df is not None:
                cache_set(key, df, ttl_seconds=300)
        return df

    # ─────────────────────────────────────────
    # LEVEL CALCULATION (non-SPY)
    # ─────────────────────────────────────────

    def _calculate_levels(self, df, ma_result, donchian_result, direction):
        try:
            close  = float(df["close"].iloc[-1])
            ma20   = ma_result.get("ma20",  close * 0.99)
            recent = df.tail(20)

            if direction == "bullish":
                entry      = close
                swing_low  = float(recent["low"].min())
                stop       = max(swing_low, ma20 * 0.995)
                rng        = float(recent["high"].max()) - float(recent["low"].min())
                target     = round(entry + rng * 1.5, 2)
                exit_type  = "trail_stop" if donchian_result.get("breakout_up") else "structure"
            else:
                entry      = close
                swing_high = float(recent["high"].max())
                stop       = min(swing_high, ma20 * 1.005)
                rng        = float(recent["high"].max()) - float(recent["low"].min())
                target     = round(entry - rng * 1.5, 2)
                exit_type  = "trail_stop" if donchian_result.get("breakout_down") else "structure"

            if direction == "bullish" and not (stop < entry < target):
                return None, None, None, None
            if direction == "bearish" and not (target < entry < stop):
                return None, None, None, None

            return round(entry, 2), round(stop, 2), round(target, 2), exit_type

        except Exception as e:
            logger.error(f"Level calculation error: {e}")
            return None, None, None, None

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _load_watchlist(self) -> dict:
        try:
            with open(config.WATCHLIST_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load watchlist: {e}")
            return {"swing": ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]}


# ── Standalone test ──────────────────────────────────────────

if __name__ == "__main__":
    print("Running swing scanner in standalone mode...")
    scanner = SwingScanner()
    results = scanner.run()
    print(f"\nTotal alerts: {len(results)}")
