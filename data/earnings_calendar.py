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

Data source:
    PolygonClient.get_ticker_details(ticker).next_earnings_date

    Polygon's free Stocks Starter tier includes ticker details. If the
    field is missing for a ticker, that ticker is silently dropped from
    the list (matches existing _check_earnings behavior).

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
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger


CACHE_FILE_NAME = "earnings_calendar.json"


class EarningsCalendar:
    """
    Aggregates upcoming earnings dates across the watchlist.

    A 'cycle' is one full refresh: hits Polygon once per watchlist
    ticker. Cached on disk for `cache_ttl_days` (default 1 day) so
    re-fetching is free unless explicitly forced.
    """

    def __init__(
        self,
        polygon_client       = None,
        watchlist_path: str  = None,
        cache_ttl_days: int  = 1,
    ):
        self.polygon         = polygon_client
        self.watchlist_path  = watchlist_path or config.WATCHLIST_PATH
        self.cache_ttl_days  = cache_ttl_days

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
        elif self.polygon is None:
            # No fetcher -- serve whatever cache we have rather than
            # overwriting it with an empty refresh.
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

    def _refresh(self) -> list[dict]:
        if self.polygon is None:
            logger.warning("EarningsCalendar: no polygon_client -- skipping refresh")
            return []

        tickers = self._load_watchlist()
        out: list[dict] = []
        for ticker in tickers:
            ed = self._fetch_next_earnings_date(ticker)
            if ed:
                out.append({"ticker": ticker, "earnings_date": ed})
        logger.info(
            f"EarningsCalendar: fetched {len(out)}/{len(tickers)} earnings dates"
        )
        return out

    def _fetch_next_earnings_date(self, ticker: str) -> Optional[str]:
        """One ticker, one Polygon call. Returns YYYY-MM-DD or None."""
        try:
            details = self.polygon.get_ticker_details(ticker)
            if details is None:
                return None
            raw = getattr(details, "next_earnings_date", None)
            if raw is None:
                return None
            if isinstance(raw, str):
                return raw
            # date / datetime
            return raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
        except Exception as e:
            logger.warning(f"EarningsCalendar: {ticker} fetch failed: {e}")
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
