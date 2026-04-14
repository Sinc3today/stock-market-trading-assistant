"""
scanners/intraday_scanner.py — Intraday Scanner
Runs every 5 minutes during market hours (9:30 AM - 4:00 PM EST).
Scores intraday watchlist on 15min + 5min timeframes.
Weights RVOL and CVD more heavily for intraday context.

Usage:
    from scanners.intraday_scanner import IntradayScanner
    scanner = IntradayScanner()
    scanner.run()
"""

import json
import sys
import os
from datetime import datetime, time
import pytz
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from data.polygon_client import PolygonClient
from data.alpaca_client import AlpacaClient
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


class IntradayScanner:
    """
    Scans the intraday watchlist on 15min and 5min timeframes.
    Only runs during market hours.
    Higher weight on RVOL and CVD vs swing scanner.
    """

    def __init__(self):
        self.client        = PolygonClient()
        self.alpaca_client = AlpacaClient()
        self.scorer        = SignalScorer()
        self.gates   = AlertGates()
        self.builder = AlertBuilder()
        self.logger  = TradeLogger()
        self.discord_post_fn = None
        self.eastern = pytz.timezone("US/Eastern")

    def set_discord_fn(self, fn):
        self.discord_post_fn = fn

    def is_market_hours(self) -> bool:
        """Return True if current time is within market hours EST."""
        now_est = datetime.now(self.eastern).time()
        market_open  = time(9, 30)
        market_close = time(16, 0)
        return market_open <= now_est <= market_close

    def run(self) -> list[dict]:
        """
        Run the full intraday scan.
        Skips automatically outside market hours.
        Returns list of alerts that fired.
        """
        if not self.is_market_hours():
            logger.debug("Outside market hours — intraday scan skipped")
            return []

        watchlist = self._load_watchlist()
        tickers   = watchlist.get("intraday", [])

        if not tickers:
            logger.warning("Intraday watchlist is empty — nothing to scan")
            return []

        now_str = datetime.now(self.eastern).strftime("%I:%M %p EST")
        logger.info(f"Starting intraday scan at {now_str} — {len(tickers)} tickers")

        fired_alerts = []
        for ticker in tickers:
            try:
                alert = self._scan_ticker(ticker)
                if alert:
                    fired_alerts.append(alert)
            except Exception as e:
                logger.error(f"Intraday scan error for {ticker}: {e}")
                continue

        logger.info(
            f"Intraday scan complete — "
            f"{len(fired_alerts)} alerts from {len(tickers)} tickers"
        )
        return fired_alerts

    # ─────────────────────────────────────────
    # CORE SCAN LOGIC
    # ─────────────────────────────────────────

    def _scan_ticker(self, ticker: str) -> dict | None:
        """Score a single ticker on 15min + 5min timeframes."""
        logger.debug(f"Intraday scanning {ticker}...")

        # ── Fetch primary (15min) via Alpaca ─────────────────────
        cache_key = f"{ticker}_{config.INTRADAY_PRIMARY_TIMEFRAME}_intraday"
        df_15 = cache_get(cache_key)
        if df_15 is None:
            df_15 = self.alpaca_client.get_bars(
                ticker,
                timeframe=config.INTRADAY_PRIMARY_TIMEFRAME,
                limit=200, days_back=10
            )
            if df_15 is not None:
                cache_set(cache_key, df_15, ttl_seconds=60)

        if df_15 is None or len(df_15) < 15:
            logger.warning(f"Insufficient 15min data for {ticker} — got {len(df_15) if df_15 is not None else 0} bars")
            return None

        # ── Fetch secondary (5min) ───────────────────────────────
        cache_key_5 = f"{ticker}_{config.INTRADAY_SECONDARY_TIMEFRAME}_intraday"
        df_5 = cache_get(cache_key_5)
        if df_5 is None:
            df_5 = self.alpaca_client.get_bars(
                ticker,
                timeframe=config.INTRADAY_SECONDARY_TIMEFRAME,
                limit=100, days_back=5
            )
            if df_5 is not None:
                cache_set(cache_key_5, df_5, ttl_seconds=60)

        # ── Run indicators on 15min ──────────────────────────────
        ma_r   = MovingAverages(df_15).analyze()
        dc_r   = DonchianChannels(df_15, period=config.DONCHIAN_INTRADAY_PERIOD).analyze()
        vol_r  = VolumeAnalysis(df_15).analyze()
        cvd_r  = CVDAnalysis(df_15).analyze()
        rsi_r  = RSIAnalysis(df_15).analyze()

        # ── Intraday RVOL bonus ──────────────────────────────────
        # RVOL is more important intraday — add bonus pts if spike is strong
        rvol = vol_r.get("rvol", 0) or 0
        rvol_bonus = 6 if rvol >= 2.0 else (3 if rvol >= 1.5 else 0)

        # ── Confluence check on 5min ─────────────────────────────
        confluence     = False
        confluence_tfs = [config.INTRADAY_PRIMARY_TIMEFRAME]

        if df_5 is not None and len(df_5) >= 15:
            ma_r_5 = MovingAverages(df_5).analyze()
            if ma_r_5.get("trend_direction") == ma_r.get("trend_direction") \
               and ma_r.get("trend_direction") != "neutral":
                confluence     = True
                confluence_tfs = [
                    config.INTRADAY_PRIMARY_TIMEFRAME,
                    config.INTRADAY_SECONDARY_TIMEFRAME
                ]

        # ── Score ────────────────────────────────────────────────
        score_result = self.scorer.score(
            ma_r, dc_r, vol_r, cvd_r, rsi_r,
            rvol_bonus=rvol_bonus,
            confluence=confluence,
        )

        score = score_result["final_score"]
        tier  = score_result["tier"]

        if tier in ("none", "watchlist"):
            logger.debug(f"{ticker} intraday → {tier} ({score}/100)")
            return None

        # ── Calculate intraday trade levels ──────────────────────
        entry, stop, target, exit_type = self._calculate_intraday_levels(
            df_15, ma_r, dc_r, score_result["direction"]
        )

        if entry is None:
            return None

        # ── Gates ────────────────────────────────────────────────
        passed, failures, gate_data = self.gates.check(
            score_result, ticker, entry, stop, target
        )

        if not passed:
            logger.debug(f"{ticker} intraday gates failed: {failures}")
            return None

        # ── Build + fire alert ───────────────────────────────────
        alert = self.builder.build(
            ticker=ticker,
            timeframe=config.INTRADAY_PRIMARY_TIMEFRAME,
            mode="intraday",
            score_result=score_result,
            gate_data=gate_data,
            ma_result=ma_r, donchian_result=dc_r,
            volume_result=vol_r, cvd_result=cvd_r, rsi_result=rsi_r,
            entry=entry, stop=stop, target=target,
            exit_type=exit_type,
            confluence_timeframes=confluence_tfs,
        )

        message = self.builder.format_discord_message(alert)
        self.logger.log_alert(alert)

        if self.discord_post_fn:
            self.discord_post_fn(alert, message)

        logger.info(
            f"🔔 INTRADAY ALERT — {ticker} | "
            f"Score: {score} | {score_result['direction'].upper()} | "
            f"RVOL: {rvol}x"
        )
        return alert

    # ─────────────────────────────────────────
    # INTRADAY LEVEL CALCULATION
    # ─────────────────────────────────────────

    def _calculate_intraday_levels(
        self,
        df,
        ma_result:       dict,
        donchian_result: dict,
        direction:       str,
    ) -> tuple:
        """
        Tighter levels for intraday trades.
        Uses last 10 bars for structure instead of 20.
        """
        try:
            close  = float(df["close"].iloc[-1])
            ma20   = ma_result.get("ma20", close)
            recent = df.tail(10)

            if direction == "bullish":
                entry      = close
                swing_low  = float(recent["low"].min())
                stop       = round(min(swing_low, ma20 * 0.998), 2)
                bar_range  = float(recent["high"].max()) - float(recent["low"].min())
                target     = round(entry + bar_range, 2)  # 1:1 range projection intraday
                exit_type  = "fixed_pct"

            else:
                entry      = close
                swing_high = float(recent["high"].max())
                stop       = round(max(swing_high, ma20 * 1.002), 2)
                bar_range  = float(recent["high"].max()) - float(recent["low"].min())
                target     = round(entry - bar_range, 2)
                exit_type  = "fixed_pct"

            if direction == "bullish" and not (stop < entry < target):
                return None, None, None, None
            if direction == "bearish" and not (target < entry < stop):
                return None, None, None, None

            return entry, stop, target, exit_type

        except Exception as e:
            logger.error(f"Intraday level calculation error: {e}")
            return None, None, None, None

    def _load_watchlist(self) -> dict:
        try:
            with open(config.WATCHLIST_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load watchlist: {e}")
            return {"swing": [], "intraday": []}