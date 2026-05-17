"""
signals/sector_breadth.py -- SPDR sector rotation + breadth tracker.

For each of the 10 SPDR sector ETFs, computes relative strength vs SPY at
multiple horizons (1d / 5d / 20d). Surfaces three useful aggregates:

    leaders / laggards     -- top 3 / bottom 3 sectors by 20d RS vs SPY
    dispersion_score()     -- stdev of 20d RS across all sectors
    regime_signal()        -- "trending_aligned" / "rotating" / "dispersed"

Interpretation:
    LOW dispersion + most sectors UP   -> trending up (skip iron condors)
    LOW dispersion + most sectors DOWN -> trending down (defensive)
    HIGH dispersion (rotation)         -> chop (iron condor candidates)

This module does NOT mutate `signals.regime_detector` -- those thresholds
are tuned and locked. Instead the dispersion signal is exposed via the
dashboard, posted to Discord as a daily briefing, and added to the
knowledge base on regime flips so the self-learning loop can correlate
it with prediction accuracy over time.

Usage:
    from signals.sector_breadth import SectorBreadth
    from data.polygon_client    import PolygonClient

    sb = SectorBreadth(PolygonClient())
    snap = sb.snapshot()
        # {
        #   "leaders":   [("XLK", 4.2), ("XLY", 3.8), ...],
        #   "laggards":  [("XLE", -3.1), ("XLU", -2.4), ...],
        #   "dispersion": 2.85,
        #   "signal":    "rotating",
        #   "rs":        {"XLK": 4.2, "XLF": 1.5, ...},
        #   "asof":      "2026-05-16T20:00:00",
        # }
"""

from __future__ import annotations

import os
import statistics
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

import config


# ── SPDR Select Sector ETFs ──────────────────────────────────────────
SECTORS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Health Care",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
}

# Relative-strength lookback periods (trading days).
RS_HORIZONS = (1, 5, 20)
DEFAULT_HORIZON = 20

# ── Dispersion thresholds ────────────────────────────────────────────
# Std deviation of 20d RS-vs-SPY across all 10 sectors.
# Calibrated from observed 5y SPY history -- adjust after a backtest pass.
DISPERSION_TIGHT_MAX = 1.50    # < 1.50 = sectors moving together
DISPERSION_WIDE_MIN  = 3.00    # > 3.00 = real rotation / chop

# Direction of "trending aligned" — at least this many sectors with same sign
TRENDING_ALIGNMENT_MIN = 7     # >= 7 of 10 same direction = aligned


class SectorBreadth:
    """SPDR sector relative-strength + dispersion tracker."""

    def __init__(self, polygon_client):
        self.polygon = polygon_client

    # ── PUBLIC API ────────────────────────────────────────

    def compute_relative_strength(
        self,
        days:      int = DEFAULT_HORIZON,
        benchmark: str = "SPY",
    ) -> Optional[dict[str, float]]:
        """
        For each sector ETF, return its `days`-period return minus SPY's
        return over the same window, expressed in percentage points.

        Returns None if SPY data is unavailable. Individual sector misses
        are skipped (warned) so a single bad ticker doesn't kill the whole
        snapshot.
        """
        # Need enough bars on each side; days_back buffers weekends.
        spy_ret = self._period_return(benchmark, days)
        if spy_ret is None:
            logger.warning(f"SectorBreadth: benchmark {benchmark} return unavailable")
            return None

        rs: dict[str, float] = {}
        for ticker in SECTORS:
            sector_ret = self._period_return(ticker, days)
            if sector_ret is None:
                continue
            rs[ticker] = round(sector_ret - spy_ret, 3)

        if not rs:
            return None
        return rs

    def dispersion_score(self, rs: dict[str, float] | None = None) -> Optional[float]:
        """
        Standard deviation of relative-strength values across sectors.

        Low dispersion = sectors moving with SPY (trending or paralysed).
        High dispersion = real rotation, often correlated with chop
        regimes that favor iron condors.
        """
        rs = rs if rs is not None else self.compute_relative_strength()
        if not rs or len(rs) < 3:
            return None
        return round(statistics.pstdev(rs.values()), 3)

    def leaders_and_laggards(
        self,
        rs: dict[str, float] | None = None,
        n:  int = 3,
    ) -> Optional[tuple[list[tuple[str, float]], list[tuple[str, float]]]]:
        """
        Return (leaders, laggards). Each is a list of (ticker, rs) sorted
        by RS descending (leaders) / ascending (laggards).
        """
        rs = rs if rs is not None else self.compute_relative_strength()
        if not rs:
            return None
        ranked   = sorted(rs.items(), key=lambda kv: kv[1], reverse=True)
        leaders  = ranked[:n]
        laggards = list(reversed(ranked[-n:]))
        return leaders, laggards

    def regime_signal(
        self,
        rs:          dict[str, float] | None = None,
        dispersion:  float | None = None,
    ) -> str:
        """
        Collapse the breadth picture into one of:

            "trending_aligned"   sectors moving together with SPY
            "rotating"           moderate dispersion, no clear leadership
            "dispersed"          high dispersion -- chop regime
            "unknown"            data unavailable

        Note: "trending_aligned" is itself directional -- the dashboard
        and KB entries should also surface leaders/laggards so callers
        can tell up-trend from down-trend.
        """
        rs = rs if rs is not None else self.compute_relative_strength()
        if not rs:
            return "unknown"
        d = dispersion if dispersion is not None else self.dispersion_score(rs)
        if d is None:
            return "unknown"

        if d >= DISPERSION_WIDE_MIN:
            return "dispersed"

        # Count sectors with same-sign RS vs SPY -- aligned trends usually
        # have most sectors on the same side.
        pos = sum(1 for v in rs.values() if v > 0)
        neg = sum(1 for v in rs.values() if v < 0)
        if d <= DISPERSION_TIGHT_MAX and max(pos, neg) >= TRENDING_ALIGNMENT_MIN:
            return "trending_aligned"
        return "rotating"

    def snapshot(self, days: int = DEFAULT_HORIZON) -> dict:
        """One-call summary for KB / dashboard / Discord."""
        rs = self.compute_relative_strength(days=days)
        if not rs:
            return {
                "leaders":    [],
                "laggards":   [],
                "dispersion": None,
                "signal":     "unknown",
                "rs":         {},
                "asof":       datetime.now().isoformat(timespec="seconds"),
                "horizon":    days,
            }

        ll = self.leaders_and_laggards(rs) or ([], [])
        leaders, laggards = ll
        dispersion = self.dispersion_score(rs)
        signal     = self.regime_signal(rs, dispersion)
        return {
            "leaders":    leaders,
            "laggards":   laggards,
            "dispersion": dispersion,
            "signal":     signal,
            "rs":         rs,
            "asof":       datetime.now().isoformat(timespec="seconds"),
            "horizon":    days,
        }

    # ── PRIVATE ────────────────────────────────────────────

    def _period_return(self, ticker: str, days: int) -> Optional[float]:
        """
        Percentage return over the last `days` trading days.
        Pulls a small daily-bar window from Polygon. Returns None on
        any fetch / data failure (logged at warning level).
        """
        try:
            df = self.polygon.get_bars(
                ticker,
                timeframe = config.SWING_PRIMARY_TIMEFRAME,
                limit     = days + 5,
                days_back = days + 15,        # buffer for weekends + holidays
            )
        except Exception as e:
            logger.warning(f"SectorBreadth: {ticker} get_bars failed: {e}")
            return None

        if df is None or len(df) < 2:
            return None
        closes = df["close"]
        # Use the close `days` bars ago (or the earliest available if shorter)
        start_idx = max(0, len(closes) - days - 1)
        start_close = float(closes.iloc[start_idx])
        end_close   = float(closes.iloc[-1])
        if start_close == 0:
            return None
        return (end_close - start_close) / start_close * 100.0
