"""
data/earnings_history.py -- Per-ticker post-earnings reaction history.

Looks at each past earnings date for a ticker and records the *next-day*
close-to-close % move. Aggregates that into a "how violent does this
name move on earnings?" stat so we can:

  - Annotate upcoming earnings in the morning brief
    ("AAPL reports tomorrow; typical reaction ±2.1%")
  - Inform sizing or block aggressive trades on high-vol reactors
    (gates integration is a follow-up — this module just produces data).

Sources:
    Past earnings dates: yfinance.Ticker(t).earnings_dates  (index = date)
    Daily bars:          data.polygon_client.PolygonClient.get_bars

Cache:
    logs/earnings_history.json   refreshed every `cache_ttl_days` (30)

The polygon dependency is injectable so tests don't hit the network.
The yfinance dependency is wrapped in `_fetcher` for the same reason.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from statistics import mean, pstdev
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger


CACHE_FILE_NAME = "earnings_history.json"
DEFAULT_LOOKBACK_QUARTERS = 8       # 2 years of earnings ≈ 8 reports
DEFAULT_TTL_DAYS          = 30

# Mean absolute next-day move thresholds for the gap class label.
# Values are %-moves (so 3.0 = 3% one-day move on average).
CALM_MAX     = 1.5
NORMAL_MAX   = 3.5
# > NORMAL_MAX is "volatile"


class EarningsHistory:
    """
    Computes the typical next-day % move after a ticker's earnings.

    Public surface:
        get_reactions(ticker)  -> {ticker, n, mean_abs_move_pct,
                                    stdev_move_pct, gap_class,
                                    reactions: [{date, move_pct}, ...]}
        annotate_upcoming(upcoming) — merges reaction stats into an
                                      EarningsCalendar.get_upcoming() list.
    """

    def __init__(
        self,
        polygon_client       = None,
        cache_ttl_days: int  = DEFAULT_TTL_DAYS,
        lookback_quarters: int = DEFAULT_LOOKBACK_QUARTERS,
        date_fetcher         = None,   # tests: fn(ticker) -> list[date]
        bars_fetcher         = None,   # tests: fn(ticker, days_back) -> DataFrame
    ):
        self.polygon            = polygon_client
        self.cache_ttl_days     = cache_ttl_days
        self.lookback_quarters  = lookback_quarters
        self._date_fetcher      = date_fetcher
        self._bars_fetcher      = bars_fetcher

    # ── PUBLIC API ────────────────────────────────────

    def get_reactions(
        self, ticker: str, refresh: bool = False
    ) -> Optional[dict]:
        """Return reaction stats for one ticker, using cache when fresh."""
        ticker = ticker.upper()
        cache  = self._load_cache()
        entry  = cache.get(ticker)
        if not refresh and self._is_fresh(entry):
            return entry["data"]

        data = self._compute(ticker)
        if data is None:
            return entry["data"] if entry else None

        cache[ticker] = {
            "fetched_at": date.today().isoformat(),
            "data":       data,
        }
        self._save_cache(cache)
        return data

    def annotate_upcoming(self, upcoming: list[dict]) -> list[dict]:
        """
        Given the output of EarningsCalendar.get_upcoming(), enrich each
        item with `mean_abs_move_pct`, `stdev_move_pct`, `gap_class` if
        the ticker has cached reaction history. Unknown tickers are
        passed through unchanged (no field added).
        """
        out: list[dict] = []
        for e in upcoming:
            enriched = dict(e)
            stats    = self.get_reactions(e.get("ticker", ""))
            if stats:
                enriched["mean_abs_move_pct"] = stats["mean_abs_move_pct"]
                enriched["stdev_move_pct"]    = stats["stdev_move_pct"]
                enriched["gap_class"]         = stats["gap_class"]
            out.append(enriched)
        return out

    # ── COMPUTE ───────────────────────────────────────

    def _compute(self, ticker: str) -> Optional[dict]:
        dates = self._fetch_past_dates(ticker)
        if not dates:
            return None
        bars = self._fetch_bars(ticker, days_back=400)
        if bars is None or len(bars) == 0:
            return None

        reactions = []
        for d in dates:
            move = self._next_day_move(bars, d)
            if move is None:
                continue
            reactions.append({"date": d.isoformat(),
                              "move_pct": round(move, 3)})

        if not reactions:
            return None

        abs_moves = [abs(r["move_pct"]) for r in reactions]
        mean_abs  = round(mean(abs_moves), 3)
        std_move  = round(pstdev([r["move_pct"] for r in reactions])
                          if len(reactions) > 1 else 0.0, 3)

        return {
            "ticker":            ticker,
            "n":                 len(reactions),
            "mean_abs_move_pct": mean_abs,
            "stdev_move_pct":    std_move,
            "gap_class":         self._classify(mean_abs),
            "reactions":         reactions,
        }

    @staticmethod
    def _classify(mean_abs: float) -> str:
        if mean_abs < CALM_MAX:
            return "calm"
        if mean_abs < NORMAL_MAX:
            return "normal"
        return "volatile"

    @staticmethod
    def _next_day_move(bars, earnings_date: date) -> Optional[float]:
        """
        % move from earnings-day close to next-trading-day close.
        bars: DataFrame indexed by timestamp with a 'close' column.
        """
        # Normalize to dates so we can do exact matches regardless of TZ.
        dates_index = [t.date() if hasattr(t, "date") else t for t in bars.index]
        try:
            i = dates_index.index(earnings_date)
        except ValueError:
            # The earnings day itself isn't in the bars (could be a holiday
            # quirk or it's tomorrow). Fall back to the prior trading day.
            prior = [(idx, d) for idx, d in enumerate(dates_index) if d < earnings_date]
            if not prior:
                return None
            i, _ = max(prior, key=lambda x: x[1])
        if i + 1 >= len(bars):
            return None
        close_t  = float(bars["close"].iloc[i])
        close_tp = float(bars["close"].iloc[i + 1])
        if close_t == 0:
            return None
        return (close_tp - close_t) / close_t * 100

    # ── FETCHERS ──────────────────────────────────────

    def _fetch_past_dates(self, ticker: str) -> list[date]:
        if self._date_fetcher is not None:
            return self._date_fetcher(ticker) or []
        try:
            import yfinance as yf
            ed = yf.Ticker(ticker).earnings_dates
            if ed is None or len(ed) == 0:
                return []
            today = date.today()
            dates = []
            for t in ed.index:
                d = t.date() if hasattr(t, "date") else t
                if isinstance(d, date) and d < today:
                    dates.append(d)
            dates.sort(reverse=True)
            return dates[: self.lookback_quarters]
        except Exception as e:
            logger.warning(f"EarningsHistory: yfinance {ticker} dates failed: {e}")
            return []

    def _fetch_bars(self, ticker: str, days_back: int):
        if self._bars_fetcher is not None:
            return self._bars_fetcher(ticker, days_back)
        if self.polygon is None:
            logger.warning("EarningsHistory: polygon_client not injected; cannot fetch bars")
            return None
        try:
            return self.polygon.get_bars(
                ticker, timeframe="day", limit=days_back + 10, days_back=days_back,
            )
        except Exception as e:
            logger.warning(f"EarningsHistory: polygon bars {ticker} failed: {e}")
            return None

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
            logger.warning(f"EarningsHistory: cache read failed: {e}")
            return {}

    def _save_cache(self, cache: dict) -> None:
        try:
            with open(self._cache_path(), "w") as f:
                json.dump(cache, f, indent=2)
        except OSError as e:
            logger.warning(f"EarningsHistory: cache write failed: {e}")

    def _is_fresh(self, entry: Optional[dict]) -> bool:
        if not entry or "fetched_at" not in entry:
            return False
        try:
            fetched = date.fromisoformat(entry["fetched_at"])
        except ValueError:
            return False
        return (date.today() - fetched).days < self.cache_ttl_days
