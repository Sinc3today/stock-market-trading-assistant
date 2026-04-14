"""
scanners/swing_scanner.py — Swing Trade Scanner
Runs every morning at 9:00 AM EST before market open.
Scores every ticker in the swing watchlist on Daily + 4H timeframes.
Fires alerts through Discord and logs to journal.

Usage:
    from scanners.swing_scanner import SwingScanner
    scanner = SwingScanner()
    scanner.run()
"""

import json
import sys
import os
from datetime import datetime
from loguru import logger

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
from journal.trade_logger import TradeLogger


class SwingScanner:
    """
    Scans the swing watchlist on Daily and 4H timeframes.
    Checks for confluence between timeframes for bonus scoring.
    Calculates entry/stop/target from price structure.
    """

    def __init__(self):
        self.client   = PolygonClient()
        self.scorer   = SignalScorer()
        self.gates    = AlertGates()
        self.builder  = AlertBuilder()
        self.logger   = TradeLogger()
        self.discord_post_fn = None  # Injected by main.py

    def set_discord_fn(self, fn):
        """Inject the Discord posting function from main.py"""
        self.discord_post_fn = fn

    def run(self) -> list[dict]:
        """
        Run the full swing scan.
        Returns list of alerts that fired.
        """
        watchlist = self._load_watchlist()
        tickers   = watchlist.get("swing", [])

        if not tickers:
            logger.warning("Swing watchlist is empty — nothing to scan")
            return []

        logger.info(f"Starting swing scan — {len(tickers)} tickers: {tickers}")
        fired_alerts = []

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
    # CORE SCAN LOGIC
    # ─────────────────────────────────────────

    def _scan_ticker(self, ticker: str) -> dict | None:
        """
        Score a single ticker on primary (daily) and secondary (4H) timeframes.
        Returns alert dict if gates pass, None otherwise.
        """
        logger.info(f"Scanning {ticker}...")

        # ── Fetch data ───────────────────────────────────────────
        cache_key_primary   = f"{ticker}_{config.SWING_PRIMARY_TIMEFRAME}"
        cache_key_secondary = f"{ticker}_{config.SWING_SECONDARY_TIMEFRAME}"

        df_primary = cache_get(cache_key_primary)
        if df_primary is None:
            df_primary = self.client.get_bars(
                ticker,
                timeframe=config.SWING_PRIMARY_TIMEFRAME,
                limit=300, days_back=400
            )
            if df_primary is not None:
                cache_set(cache_key_primary, df_primary, ttl_seconds=300)

        if df_primary is None or len(df_primary) < 50:
            logger.warning(f"Insufficient data for {ticker} — skipping")
            return None

        df_secondary = cache_get(cache_key_secondary)
        if df_secondary is None:
            df_secondary = self.client.get_bars(
                ticker,
                timeframe=config.SWING_SECONDARY_TIMEFRAME,
                limit=300, days_back=100
            )
            if df_secondary is not None:
                cache_set(cache_key_secondary, df_secondary, ttl_seconds=300)

        # ── Run indicators on primary timeframe ──────────────────
        ma_r   = MovingAverages(df_primary).analyze()
        dc_r   = DonchianChannels(df_primary).analyze()
        vol_r  = VolumeAnalysis(df_primary).analyze()
        cvd_r  = CVDAnalysis(df_primary).analyze()
        rsi_r  = RSIAnalysis(df_primary).analyze()

        # ── Check confluence on secondary timeframe ──────────────
        confluence        = False
        confluence_tfs    = [config.SWING_PRIMARY_TIMEFRAME]

        if df_secondary is not None and len(df_secondary) >= 50:
            ma_r2  = MovingAverages(df_secondary).analyze()
            dc_r2  = DonchianChannels(df_secondary).analyze()
            rsi_r2 = RSIAnalysis(df_secondary).analyze()

            # Confluence = same trend direction on both timeframes
            if ma_r2.get("trend_direction") == ma_r.get("trend_direction") \
               and ma_r.get("trend_direction") != "neutral":
                confluence     = True
                confluence_tfs = [
                    config.SWING_PRIMARY_TIMEFRAME,
                    config.SWING_SECONDARY_TIMEFRAME
                ]
                logger.info(f"{ticker} confluence confirmed on {confluence_tfs}")

        # ── Score ────────────────────────────────────────────────
        score_result = self.scorer.score(
            ma_r, dc_r, vol_r, cvd_r, rsi_r,
            confluence=confluence
        )

        score = score_result["final_score"]
        tier  = score_result["tier"]

        # Log watchlist candidates
        if tier == "watchlist":
            watchlist_alert = self.builder.build(
                ticker=ticker,
                timeframe=config.SWING_PRIMARY_TIMEFRAME,
                mode="swing",
                score_result=score_result,
                gate_data={},
                ma_result=ma_r, donchian_result=dc_r,
                volume_result=vol_r, cvd_result=cvd_r, rsi_result=rsi_r,
                entry=0, stop=0, target=0,
                exit_type="tbd",
                confluence_timeframes=confluence_tfs,
            )
            self.logger.log_watchlist_entry(watchlist_alert)
            logger.info(f"{ticker} → Watchlist ({score}/100)")
            return None

        if tier == "none":
            logger.info(f"{ticker} → No signal ({score}/100)")
            return None

        # ── Calculate trade levels ───────────────────────────────
        entry, stop, target, exit_type = self._calculate_levels(
            df_primary, ma_r, dc_r, score_result["direction"]
        )

        if entry is None:
            logger.warning(f"{ticker} → Could not calculate trade levels")
            return None

        # ── Run gates ────────────────────────────────────────────
        passed, failures, gate_data = self.gates.check(
            score_result, ticker, entry, stop, target
        )

        if not passed:
            logger.info(f"{ticker} → Gates failed: {failures}")
            return None

        # ── Build + fire alert ───────────────────────────────────
        alert = self.builder.build(
            ticker=ticker,
            timeframe=config.SWING_PRIMARY_TIMEFRAME,
            mode="swing",
            score_result=score_result,
            gate_data=gate_data,
            ma_result=ma_r, donchian_result=dc_r,
            volume_result=vol_r, cvd_result=cvd_r, rsi_result=rsi_r,
            entry=entry, stop=stop, target=target,
            exit_type=exit_type,
            confluence_timeframes=confluence_tfs,
        )

        message = self.builder.format_discord_message(alert)

        # Log to journal
        self.logger.log_alert(alert)

        # Post to Discord
        if self.discord_post_fn:
            self.discord_post_fn(alert, message)

        logger.info(
            f"🔔 ALERT FIRED — {ticker} | "
            f"Score: {score} | {score_result['direction'].upper()} | "
            f"R/R: {gate_data.get('rr_ratio', 0):.2f}:1"
        )
        return alert

    # ─────────────────────────────────────────
    # TRADE LEVEL CALCULATION
    # ─────────────────────────────────────────

    def _calculate_levels(
        self,
        df,
        ma_result:       dict,
        donchian_result: dict,
        direction:       str,
    ) -> tuple:
        """
        Calculate entry, stop, target and exit type from price structure.

        Entry:  Current close (or Donchian breakout level)
        Stop:   Below/above nearest MA or recent swing low/high
        Target: Next significant resistance/support level
        Exit:   Assigned based on setup type
        """
        try:
            close  = float(df["close"].iloc[-1])
            ma20   = ma_result.get("ma20",  close * 0.99)
            ma50   = ma_result.get("ma50",  close * 0.97)
            ma200  = ma_result.get("ma200", close * 0.93)
            recent = df.tail(20)

            if direction == "bullish":
                # Entry: current close
                entry = close

                # Stop: below MA20, or recent swing low — whichever is closer
                swing_low  = float(recent["low"].min())
                ma_stop    = ma20 * 0.995  # Slight buffer below MA20
                stop       = max(swing_low, ma_stop)  # Closest stop = less risk

                # Target: project based on recent range
                recent_range = float(recent["high"].max()) - float(recent["low"].min())
                target       = round(entry + (recent_range * 1.5), 2)

                # Exit type based on setup
                if donchian_result.get("breakout_up"):
                    exit_type = "trail_stop"
                else:
                    exit_type = "structure"

            else:  # bearish
                entry = close

                swing_high = float(recent["high"].max())
                ma_stop    = ma20 * 1.005
                stop       = min(swing_high, ma_stop)

                recent_range = float(recent["high"].max()) - float(recent["low"].min())
                target       = round(entry - (recent_range * 1.5), 2)

                if donchian_result.get("breakout_down"):
                    exit_type = "trail_stop"
                else:
                    exit_type = "structure"

            # Validate levels make sense
            if direction == "bullish" and not (stop < entry < target):
                logger.warning(f"Invalid levels: stop={stop} entry={entry} target={target}")
                return None, None, None, None
            if direction == "bearish" and not (target < entry < stop):
                logger.warning(f"Invalid levels: target={target} entry={entry} stop={stop}")
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
            return {"swing": [], "intraday": []}