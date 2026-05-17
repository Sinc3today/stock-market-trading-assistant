"""
data/earnings_calendar.py -- Per-watchlist-ticker earnings calendar.

The existing `signals/gates.py:_check_earnings()` checks one ticker at a
time when an alert fires. This module aggregates: it walks the entire
watchlist (swing + intraday + options-enabled), pulls each ticker's
next earnings date via PolygonClient, and exposes a clean upcoming-list
view.

Used by:
    - MorningBriefer  ("AAPL earnings tomorrow" → into the brief context)
    - MacroChat       ("what's coming this week for my watchlist?")
    - /macro web page  (third card: upcoming earnings list)

Data source — IMPORTANT:
    yfinance.Ticker(symbol).calendar["Earnings Date"]

    An earlier prototype used Polygon's TickerDetails.next_earnings_date,
    but that attribute does NOT exist on Polygon's free tier (verified
    by inspection 2026-05-16). The existing signals/gates.py:_check_earnings
    has been silently returning "No earnings date available" for every
    ticker as a result. yfinance gives free, reliable earnings dates.

    The polygon_client constructor kwarg is retained for backward
    compatibility — a few callsites still pass it — but the module
    no longer uses it.

Cache:
    logs/earnings_calendar.json    refreshed at most once per day

Watchlist resolution:
    Union of `swing`, `intraday`, and `options_enabled` lists from
    `config/watchlist.json` (so the calendar covers everything we
    might trade).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger


CACHE_FILE_NAME = "earnings_calendar.json"

# Small delay between yfinance calls to stay under Yahoo's rate limiter
# during the watchlist sweep (~10 tickers × 0.5s = ~5s).
YFINANCE_DELAY_SEC = 0.5


class EarningsCalendar:
    """
    Aggregates upcoming earnings dates across the watchlist via yfinance.

    A 'cycle' is one full refresh: hits yfinance once per watchlist
    ticker. Cached on disk for `cache_ttl_days` (default 1 day) so
    re-fetching is free unless explicitly forced.
    """

    def __init__(
        self,
        polygon_client       = None,    # kept for backward-compat; ignored
        watchlist_path: str  = None,
        cache_ttl_days: int  = 1,
        fetcher              = None,    # injectable in tests: fn(ticker) -> "YYYY-MM-DD" | None
    ):
        # polygon_client is retained so existing callsites don't break
        # but the module uses yfinance. self.polygon is what the
        # "no fetcher available" path checks — we mirror that semantics
        # via self._fetcher below.
        self.polygon         = polygon_client    # legacy attr, unused
        self.watchlist_path  = watchlist_path or config.WATCHLIST_PATH
        self.cache_ttl_days  = cache_ttl_days
        self._fetcher        = fetcher           # if None, real yfinance is used

    # ── PUBLIC API ────────────────────────────────────

    def get_upcoming(self, days: int = 14, refresh: bool = False) -> list[dict]:
        """
        Return upcoming earnings within `days`, sorted by date ascending.

        Each entry: {"ticker": str, "earnings_date": "YYYY-MM-DD",
                      "days_away": int}
        """
        cache = self._load_cache()
        cache_entries = cache.get("entries", [])

        if not refresh and self._cache_is_fresh(cache):
            entries = cache_entries
        elif not self._can_fetch():
            # No fetcher available -- serve whatever cache we have rather
            # than overwriting it with an empty refresh.
            entries = cache_entries
        else:
            entries = self._refresh()
            self._save_cache(entries)

        today  = date.today()
        cutoff = today + timedelta(days=days)
        out: list[dict] = []
        for e in entries:
            ed = self._parse_date(e.get("earnings_date"))
            if ed is None:
                continue
            if today <= ed <= cutoff:
                out.append({
                    "ticker":        e["ticker"],
                    "earnings_date": ed.isoformat(),
                    "days_away":     (ed - today).days,
                })
        out.sort(key=lambda x: x["days_away"])
        return out

    def get_for_ticker(self, ticker: str, days: int = 30) -> Optional[dict]:
        """Return the next earnings entry for one ticker, or None."""
        ticker = ticker.upper()
        for e in self.get_upcoming(days=days):
            if e["ticker"] == ticker:
                return e
        return None

    def get_today_and_tomorrow(self) -> list[dict]:
        """For morning_briefer: tickers reporting in next 48h."""
        return [e for e in self.get_upcoming(days=2) if e["days_away"] <= 1]

    # ── CACHE ─────────────────────────────────────────

    def _cache_path(self) -> str:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        return os.path.join(config.LOG_DIR, CACHE_FILE_NAME)

    def _load_cache(self) -> dict:
        path = self._cache_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"EarningsCalendar: cache read failed: {e}")
            return {}

    def _save_cache(self, entries: list[dict]) -> None:
        try:
            with open(self._cache_path(), "w") as f:
                json.dump({
                    "fetched_at": date.today().isoformat(),
                    "entries":    entries,
                }, f, indent=2)
        except OSError as e:
            logger.warning(f"EarningsCalendar: cache write failed: {e}")

    def _cache_is_fresh(self, cache: dict) -> bool:
        if not cache or "fetched_at" not in cache:
            return False
        try:
            fetched = date.fromisoformat(cache["fetched_at"])
        except ValueError:
            return False
        return (date.today() - fetched).days < self.cache_ttl_days

    # ── REFRESH (hits Polygon) ────────────────────────

    def _can_fetch(self) -> bool:
        """True if we can refresh — either we have an injected fetcher
        (tests) or yfinance is importable (production)."""
        if self._fetcher is not None:
            return True
        try:
            import yfinance  # noqa: F401
            return True
        except ImportError:
            logger.warning("EarningsCalendar: yfinance not installed -- skipping refresh")
            return False

    def _refresh(self) -> list[dict]:
        tickers = self._load_watchlist()
        if not tickers:
            return []

        fetcher = self._fetcher or self._yfinance_next_earnings_date
        out: list[dict] = []
        for i, ticker in enumerate(tickers):
            if i > 0 and self._fetcher is None:
                # Rate-limit only the live yfinance path; tests use injected fetchers.
                time.sleep(YFINANCE_DELAY_SEC)
            try:
                ed = fetcher(ticker)
            except Exception as e:
                logger.warning(f"EarningsCalendar: {ticker} fetch raised: {e}")
                continue
            if ed:
                out.append({"ticker": ticker, "earnings_date": ed})
        logger.info(
            f"EarningsCalendar: fetched {len(out)}/{len(tickers)} earnings dates"
        )
        return out

    @staticmethod
    def _yfinance_next_earnings_date(ticker: str) -> Optional[str]:
        """
        One ticker, one yfinance lookup. Returns YYYY-MM-DD or None.

        yfinance.Ticker(t).calendar is a dict with an "Earnings Date" key
        whose value is a list[date]. ETFs return an empty dict.
        """
        try:
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            if not cal or not isinstance(cal, dict):
                return None
            dates = cal.get("Earnings Date") or []
            if not dates:
                return None
            # Take the soonest upcoming date
            today = date.today()
            future = [d for d in dates if isinstance(d, date) and d >= today]
            chosen = min(future) if future else dates[0]
            return chosen.isoformat() if hasattr(chosen, "isoformat") else str(chosen)
        except Exception as e:
            logger.warning(f"EarningsCalendar: yfinance {ticker} failed: {e}")
            return None

    # ── WATCHLIST ─────────────────────────────────────

    def _load_watchlist(self) -> list[str]:
        """Union of swing + intraday + options_enabled lists."""
        if not os.path.exists(self.watchlist_path):
            logger.warning(f"EarningsCalendar: watchlist {self.watchlist_path} missing")
            return []
        try:
            with open(self.watchlist_path) as f:
                wl = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"EarningsCalendar: watchlist parse failed: {e}")
            return []
        bag: set[str] = set()
        for key in ("swing", "intraday", "options_enabled"):
            for t in wl.get(key, []) or []:
                bag.add(t.upper())
        return sorted(bag)

    # ── HELPERS ───────────────────────────────────────

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None
