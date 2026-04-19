"""
scanners/swing_scanner.py

Swing scanner — runs once at 9:00 AM EST on weekdays.
Pulls daily + 4h data and runs signal engine across full watchlist.
Posts qualifying alerts to Discord.

Run standalone for testing:
    python -m scanners.swing_scanner
"""

import time
from datetime import datetime
from typing import Callable, Optional
from loguru import logger

import config
from signals.signal_engine import SignalEngine, SignalResult


# ─────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────

def _fetch_daily(ticker: str):
    """Fetch 60 days of daily OHLCV. Returns DataFrame or None."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period="60d", interval="1d",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:
            return None
        df.columns = [c.lower() for c in df.columns]
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logger.warning(f"[swing] Daily data failed for {ticker}: {e}")
        return None


def _fetch_intraday(ticker: str):
    """Fetch 5 days of 15-min OHLCV for confluence check. Returns DataFrame or None."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period="5d", interval="15m",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:
            return None
        df.columns = [c.lower() for c in df.columns]
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logger.warning(f"[swing] Intraday data failed for {ticker}: {e}")
        return None


# ─────────────────────────────────────────
# SWING SCANNER
# ─────────────────────────────────────────

class SwingScanner:
    """
    Swing scanner: evaluates daily setups for options plays.

    Attributes:
        discord_fn      — injected by main.py (post_alert_sync)
        premarket_scanner — injected by main.py for priority list
    """

    def __init__(self):
        self.engine          = SignalEngine()
        self.discord_fn: Optional[Callable] = None
        self.premarket_scanner = None

    def set_discord_fn(self, fn: Callable):
        self.discord_fn = fn

    # ── Build watchlist ──────────────────────────────────────────

    def _get_watchlist(self) -> list[str]:
        """
        Combine Tier 1 + Tier 2 from config.
        If premarket scanner has a priority list, put those first.
        """
        tickers = list(config.WATCHLIST_TIER1)

        # Add Tier 2
        for t in config.WATCHLIST_TIER2:
            if t not in tickers:
                tickers.append(t)

        # Prepend premarket priority tickers
        if self.premarket_scanner and hasattr(self.premarket_scanner, "priority_list"):
            priority = self.premarket_scanner.priority_list or []
            for t in reversed(priority):
                if t in tickers:
                    tickers.remove(t)
                tickers.insert(0, t)

        # Also try loading from watchlist.json if it exists
        try:
            import json, os
            if os.path.exists(config.WATCHLIST_PATH):
                with open(config.WATCHLIST_PATH) as f:
                    custom = json.load(f)
                for t in custom:
                    if t not in tickers:
                        tickers.append(t)
        except Exception as e:
            logger.warning(f"Could not load watchlist.json: {e}")

        return tickers

    # ── Main scan ────────────────────────────────────────────────

    def run(self) -> list[SignalResult]:
        watchlist = self._get_watchlist()
        logger.info(f"[SwingScanner] Starting scan — {len(watchlist)} tickers")

        high_conviction: list[SignalResult] = []
        standard:        list[SignalResult] = []
        watch_only:      list[SignalResult] = []

        for i, ticker in enumerate(watchlist):
            try:
                df_daily    = _fetch_daily(ticker)
                df_intraday = _fetch_intraday(ticker)

                results = self.engine.evaluate(ticker, df_daily, df_intraday)

                for r in results:
                    if r.conviction == "high":
                        high_conviction.append(r)
                    elif r.conviction == "standard":
                        standard.append(r)
                    elif r.conviction == "watch":
                        watch_only.append(r)

                # Throttle to avoid API rate limits
                if i > 0 and i % 10 == 0:
                    time.sleep(1.5)

            except Exception as e:
                logger.error(f"[SwingScanner] Error on {ticker}: {e}")
                continue

        # Sort by score descending
        high_conviction.sort(key=lambda r: r.score, reverse=True)
        standard.sort(key=lambda r: r.score, reverse=True)

        all_alerts = high_conviction + standard

        # ── Log summary ──────────────────────────────────────────
        logger.info(
            f"[SwingScanner] Done — "
            f"High: {len(high_conviction)} | "
            f"Standard: {len(standard)} | "
            f"Watch: {len(watch_only)}"
        )

        # ── Print to console ─────────────────────────────────────
        self._print_results(high_conviction, standard, watch_only)

        # ── Post to Discord ──────────────────────────────────────
        if self.discord_fn:
            self._post_to_discord(high_conviction, standard)

        return all_alerts

    # ── Output helpers ───────────────────────────────────────────

    def _print_results(
        self,
        high: list[SignalResult],
        standard: list[SignalResult],
        watch: list[SignalResult],
    ):
        print("\n" + "=" * 60)
        print(f"  🔥 HIGH CONVICTION ({len(high)})")
        print("=" * 60)
        for r in high:
            print(f"  {r.ticker:6s} | {r.setup:14s} | Score: {r.score:3d} | {', '.join(r.reasons[:3])}")

        print("\n" + "=" * 60)
        print(f"  📌 STANDARD ALERTS ({len(standard)})")
        print("=" * 60)
        for r in standard:
            print(f"  {r.ticker:6s} | {r.setup:14s} | Score: {r.score:3d} | {', '.join(r.reasons[:3])}")

        if watch:
            print(f"\n  👀 WATCHLIST ({len(watch)}) — below alert threshold")
            for r in watch:
                print(f"  {r.ticker:6s} | {r.setup:14s} | Score: {r.score:3d}")
        print()

    def _post_to_discord(
        self,
        high: list[SignalResult],
        standard: list[SignalResult],
    ):
        """Post alerts to Discord channels."""
        # High conviction → dedicated channel
        for r in high:
            try:
                msg = r.to_discord_msg()
                self.discord_fn(
                    msg,
                    channel_id=config.DISCORD_CHANNEL_ID_HIGH_CONVICTION
                )
                logger.info(f"[Discord] HIGH posted: {r.ticker} {r.setup} score={r.score}")
            except Exception as e:
                logger.error(f"[Discord] Failed to post high conviction {r.ticker}: {e}")

        # Standard → standard channel
        for r in standard:
            try:
                msg = r.to_discord_msg()
                self.discord_fn(
                    msg,
                    channel_id=config.DISCORD_CHANNEL_ID_STANDARD
                )
                logger.info(f"[Discord] STANDARD posted: {r.ticker} {r.setup} score={r.score}")
            except Exception as e:
                logger.error(f"[Discord] Failed to post standard {r.ticker}: {e}")


# ── Standalone test ──────────────────────────────────────────────

if __name__ == "__main__":
    print("Running swing scanner in standalone mode...")
    scanner = SwingScanner()
    results = scanner.run()
    print(f"\nTotal alerts fired: {len(results)}")
