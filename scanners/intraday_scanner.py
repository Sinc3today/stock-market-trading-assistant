"""
scanners/intraday_scanner.py  (v2 — SPY Options Focus)

Runs every 5 minutes during market hours (9:30 AM - 4:00 PM EST).
SPY gets the full options engine (call spread / put spread / iron condor).
Other tickers use the standard scorer.

Key improvements over v1:
  - SPYOptionsEngine wired in for real-time SPY options alerts
  - Alert deduplication: won't re-alert same setup unless score improves 10+
  - Market hours check built in
  - Intraday context uses 15min primary + 5min secondary

Run standalone (outside market hours shows what WOULD fire):
    python -m scanners.intraday_scanner
"""

import json
import sys
import os
from datetime import datetime, time
from loguru import logger
import pytz

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
from signals.spy_options_engine import SPYOptionsEngine   # ← SPY engine
from journal.trade_logger import TradeLogger


EASTERN = pytz.timezone("US/Eastern")

# ── Alert dedup cache ────────────────────────────────────────
# Prevents spamming the same alert every 5 minutes
# Key: (ticker, strategy) → last score posted
_fired_cache: dict[tuple, int] = {}
RESEND_SCORE_DELTA = 10   # Only re-alert if score improves by 10+ pts


class IntradayScanner:
    """
    Intraday scanner — runs every 5 minutes during market hours.

    SPY:         Full options engine (calls / puts / iron condors)
    Other tickers: Standard scorer with intraday weighting
    """

    def __init__(self):
        self.polygon     = PolygonClient()
        self.alpaca      = AlpacaClient()
        self.scorer      = SignalScorer()
        self.gates       = AlertGates()
        self.builder     = AlertBuilder()
        self.spy_engine  = SPYOptionsEngine()
        self.logger      = TradeLogger()
        self.eastern     = EASTERN
        self.discord_post_fn = None

    def set_discord_fn(self, fn):
        """Register the Discord posting callable."""
        self.discord_post_fn = fn

    # ─────────────────────────────────────────
    # MARKET HOURS CHECK
    # ─────────────────────────────────────────

    def is_market_hours(self) -> bool:
        """Return True only on weekdays between 9:30 AM and 4:00 PM ET."""
        now_est = datetime.now(self.eastern)
        if now_est.weekday() >= 5:
            return False
        market_open  = time(9, 30)
        market_close = time(16, 0)
        return market_open <= now_est.time() <= market_close

    # ─────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────

    def run(self) -> list[dict]:
        """Scan intraday watchlist; exits immediately outside market hours."""
        if not self.is_market_hours():
            logger.debug("Outside market hours — intraday scan skipped")
            return []

        watchlist = self._load_watchlist()
        tickers   = watchlist.get("intraday", [])

        if not tickers:
            logger.warning("Intraday watchlist empty")
            return []

        now_str = datetime.now(EASTERN).strftime("%I:%M %p EST")
        logger.info(f"Intraday scan at {now_str} — {len(tickers)} tickers")

        fired = []

        # SPY gets full options engine
        if "SPY" in tickers:
            spy_alerts = self._scan_spy_intraday()
            fired.extend(spy_alerts)

        # All other tickers use standard scorer
        for ticker in tickers:
            if ticker == "SPY":
                continue
            try:
                alert = self._scan_ticker_intraday(ticker)
                if alert:
                    fired.append(alert)
            except Exception as e:
                logger.error(f"Intraday error on {ticker}: {e}")

        if fired:
            logger.info(f"Intraday scan complete — {len(fired)} alerts fired")
        return fired

    # ─────────────────────────────────────────
    # SPY INTRADAY OPTIONS ENGINE
    # ─────────────────────────────────────────

    def _scan_spy_intraday(self) -> list[dict]:
        """
        Run SPY through the options engine using intraday data.
        Primary: 15min bars  |  Secondary: 5min bars for confluence
        """
        df_15m = self._fetch_alpaca("SPY", "15min", 200, 10)
        df_5m  = self._fetch_alpaca("SPY", "5min",  100, 5)

        if df_15m is None or len(df_15m) < 20:
            logger.warning("SPY intraday: insufficient 15m data")
            return []

        setups = self.spy_engine.analyze(df_15m, df_5m)
        alerts = []

        for setup in setups:
            if setup.conviction not in ("high", "standard"):
                continue

            # Dedup check
            key        = ("SPY", setup.strategy)
            last_score = _fired_cache.get(key, 0)
            if setup.score <= last_score + RESEND_SCORE_DELTA:
                logger.debug(
                    f"SPY {setup.strategy} suppressed "
                    f"(score={setup.score}, last={last_score})"
                )
                continue

            _fired_cache[key] = setup.score

            tier  = "high_conviction" if setup.conviction == "high" else "standard"
            emoji = "🔴" if tier == "high_conviction" else "🟡"
            now   = datetime.now(EASTERN).strftime("%Y-%m-%d %I:%M %p EST")

            alert = {
                "ticker":      "SPY",
                "timestamp":   now,
                "mode":        "Intraday",
                "timeframe":   "15min",
                "direction":   setup.direction.upper() if setup.direction else "NEUTRAL",
                "tier":        tier,
                "emoji":       emoji,
                "final_score": setup.score,
                "strategy":    setup.strategy,
                "setup_tags":  setup.reasons[:4],
                "_spy_setup":  setup,
            }

            self.logger.log_alert(alert)
            discord_msg = setup.to_discord_msg()

            if self.discord_post_fn:
                self.discord_post_fn(alert, discord_msg)

            logger.info(
                f"🔔 INTRADAY SPY — {setup.strategy.upper()} | "
                f"{setup.conviction.upper()} | Score: {setup.score}"
            )
            alerts.append(alert)

        return alerts

    # ─────────────────────────────────────────
    # STANDARD INTRADAY SCAN (non-SPY)
    # ─────────────────────────────────────────

    def _scan_ticker_intraday(self, ticker: str) -> dict | None:
        df_15m = self._fetch_alpaca(ticker, "15min", 200, 10)
        df_5m  = self._fetch_alpaca(ticker, "5min",  100, 5)

        if df_15m is None or len(df_15m) < 15:
            logger.warning(f"{ticker}: insufficient intraday data")
            return None

        ma_r   = MovingAverages(df_15m).analyze()
        dc_r   = DonchianChannels(df_15m, period=config.DONCHIAN_INTRADAY_PERIOD).analyze()
        vol_r  = VolumeAnalysis(df_15m).analyze()
        cvd_r  = CVDAnalysis(df_15m).analyze()
        rsi_r  = RSIAnalysis(df_15m).analyze()

        # RVOL bonus for intraday — spikes matter more on shorter timeframes
        rvol       = vol_r.get("rvol", 0) or 0
        rvol_bonus = 6 if rvol >= 2.0 else (3 if rvol >= 1.5 else 0)

        # Confluence with 5min
        confluence = False
        confluence_tfs = [config.INTRADAY_PRIMARY_TIMEFRAME]
        if df_5m is not None and len(df_5m) >= 15:
            ma_5m = MovingAverages(df_5m).analyze()
            if ma_5m.get("trend_direction") == ma_r.get("trend_direction") \
               and ma_r.get("trend_direction") != "neutral":
                confluence     = True
                confluence_tfs = ["15min", "5min"]

        score_result = self.scorer.score(
            ma_r, dc_r, vol_r, cvd_r, rsi_r,
            rvol_bonus=rvol_bonus,
            confluence=confluence,
        )

        tier = score_result["tier"]
        if tier in ("none", "watchlist"):
            return None

        entry, stop, target, exit_type = self._calc_intraday_levels(
            df_15m, ma_r, score_result["direction"]
        )
        if entry is None:
            return None

        passed, failures, gate_data = self.gates.check(
            score_result, ticker, entry, stop, target
        )
        if not passed:
            return None

        # Dedup check
        key        = (ticker, score_result["direction"])
        last_score = _fired_cache.get(key, 0)
        if score_result["final_score"] <= last_score + RESEND_SCORE_DELTA:
            return None
        _fired_cache[key] = score_result["final_score"]

        alert   = self.builder.build(
            ticker=ticker, timeframe="15min", mode="intraday",
            score_result=score_result, gate_data=gate_data,
            ma_result=ma_r, donchian_result=dc_r,
            volume_result=vol_r, cvd_result=cvd_r, rsi_result=rsi_r,
            entry=entry, stop=stop, target=target, exit_type=exit_type,
            confluence_timeframes=confluence_tfs,
        )
        message = self.builder.format_discord_message(alert)
        self.logger.log_alert(alert)

        if self.discord_post_fn:
            self.discord_post_fn(alert, message)

        logger.info(
            f"🔔 INTRADAY — {ticker} | "
            f"Score: {score_result['final_score']} | "
            f"{score_result['direction'].upper()}"
        )
        return alert

    # ─────────────────────────────────────────
    # LEVEL CALCULATION
    # ─────────────────────────────────────────

    def _calc_intraday_levels(self, df, ma_result, direction) -> tuple:
        try:
            close  = float(df["close"].iloc[-1])
            ma20   = ma_result.get("ma20", close)
            recent = df.tail(10)

            if direction == "bullish":
                entry     = close
                stop      = round(max(float(recent["low"].min()), ma20 * 0.998), 2)
                bar_range = float(recent["high"].max()) - float(recent["low"].min())
                target    = round(entry + bar_range, 2)
                exit_type = "fixed_pct"
            else:
                entry     = close
                stop      = round(min(float(recent["high"].max()), ma20 * 1.002), 2)
                bar_range = float(recent["high"].max()) - float(recent["low"].min())
                target    = round(entry - bar_range, 2)
                exit_type = "fixed_pct"

            if direction == "bullish" and not (stop < entry < target):
                return None, None, None, None
            if direction == "bearish" and not (target < entry < stop):
                return None, None, None, None

            return entry, stop, target, exit_type
        except Exception as e:
            logger.error(f"Intraday level calc error: {e}")
            return None, None, None, None

    # ─────────────────────────────────────────
    # DATA FETCHING
    # ─────────────────────────────────────────

    def _fetch_alpaca(self, ticker, timeframe, limit, days_back):
        key = f"{ticker}_{timeframe}_intraday"
        df  = cache_get(key)
        if df is None:
            df = self.alpaca.get_bars(ticker, timeframe=timeframe,
                                      limit=limit, days_back=days_back)
            if df is not None:
                cache_set(key, df, ttl_seconds=60)
        return df

    def _load_watchlist(self) -> dict:
        try:
            with open(config.WATCHLIST_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {"intraday": ["SPY", "QQQ", "AAPL"]}


# ── Standalone test ──────────────────────────────────────────

if __name__ == "__main__":
    print("Running intraday scanner standalone...")
    print("(Outside market hours = no alerts, this is expected)")
    scanner = IntradayScanner()

    # Force run even outside hours for testing
    print("\nForcing scan outside market hours for test...\n")
    watchlist = scanner._load_watchlist()
    tickers   = watchlist.get("intraday", ["SPY"])

    if "SPY" in tickers:
        df_15m = scanner._fetch_alpaca("SPY", "15min", 200, 10)
        df_5m  = scanner._fetch_alpaca("SPY", "5min", 100, 5)
        if df_15m is not None:
            setups = scanner.spy_engine.analyze(df_15m, df_5m)
            print(f"SPY intraday setups: {len(setups)}")
            for s in setups:
                print(f"  {s.strategy} | {s.conviction} | score={s.score}")
        else:
            print("No intraday data available (market closed)")
