"""
data/vix_term_structure.py -- VIX volatility term structure module.

Pulls the four CBOE volatility indices and computes the contango ratio
(VIX / VIX3M). Used as a leading indicator for regime stress:

    contango   (VIX < VIX3M, ratio < 1.0)  -> market expects calm
    backwardation (VIX > VIX3M, > 1.0)     -> market pricing near-term stress

Historically backwardation flips have led volatility events by 1-3 days
(Feb '18, Aug '24, etc.) -- iron condor traders should be more selective
on those days. This module surfaces the signal; it does NOT mutate the
regime detector's tuned thresholds.

Data source: CBOE daily CSVs (free, no auth, no new dependency).
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX6M_History.csv

Usage:
    from data.vix_term_structure import VIXTermStructure
    ts = VIXTermStructure()
    current = ts.fetch_current()   # {"VIX9D": 13.2, "VIX": 14.1, ...}
    ratio   = ts.contango_ratio()  # 0.92  (calm)
    flag    = ts.regime_flag()     # "calm" | "cautious" | "stress" | "extreme_stress"
"""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import requests
from loguru import logger


# ── CBOE CSV endpoints (same host pattern as data/vix_client.py) ─────
CBOE_BASE = "https://cdn.cboe.com/api/global/us_indices/daily_prices"
CBOE_URLS = {
    "VIX9D": f"{CBOE_BASE}/VIX9D_History.csv",
    "VIX":   f"{CBOE_BASE}/VIX_History.csv",
    "VIX3M": f"{CBOE_BASE}/VIX3M_History.csv",
    "VIX6M": f"{CBOE_BASE}/VIX6M_History.csv",
}

# ── Contango-ratio thresholds (VIX / VIX3M) ──────────────────────────
# < 0.85  : deep contango, market expects extended calm
# 0.85-1.00: normal calm
# 1.00-1.10: cautious (slight backwardation, near-term jitters)
# 1.10-1.20: stress (real backwardation -- vol event likely)
# > 1.20  : extreme stress (vol blow-up underway)
RATIO_CALM_MAX     = 1.00
RATIO_CAUTIOUS_MAX = 1.10
RATIO_STRESS_MAX   = 1.20

# ── Cache (CBOE CSVs update once per trading day after close) ────────
_CACHE_TTL_SECONDS = 3600   # 1 hour is plenty
_cache: dict = {"current": None, "fetched_at": None}


class VIXTermStructure:
    """
    Volatility term structure fetcher + interpreter.

    All four indices are pulled in parallel from CBOE's public CSVs.
    `regime_flag()` collapses the raw ratios into one of four buckets
    that callers (regime detector, KB, dashboard) can switch on.
    """

    # ── PUBLIC API ────────────────────────────────────────

    def fetch_current(self, force: bool = False) -> Optional[dict]:
        """
        Return latest closes as {"VIX9D": float, "VIX": float, ...}.

        Cached for ~1 hour. Returns None if every CBOE CSV is unreachable
        (caller should treat as "unknown" rather than guess).
        """
        if not force and _cache["current"] and _cache["fetched_at"]:
            age = (datetime.now() - _cache["fetched_at"]).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                logger.debug(f"VIX term structure from cache (age {age:.0f}s)")
                return _cache["current"]

        out: dict[str, float] = {}
        for symbol, url in CBOE_URLS.items():
            value = self._fetch_latest_close(url)
            if value is not None:
                out[symbol] = value

        if not out:
            logger.warning("VIXTermStructure: all CBOE fetches failed")
            return None

        # Need at least VIX + VIX3M to compute the ratio downstream.
        if "VIX" not in out or "VIX3M" not in out:
            logger.warning(f"VIXTermStructure: missing core symbols ({list(out)})")
            return None

        _cache["current"]    = out
        _cache["fetched_at"] = datetime.now()
        logger.info(f"VIX term structure: {out}")
        return out

    def contango_ratio(self, current: dict | None = None) -> Optional[float]:
        """VIX / VIX3M. < 1.0 = contango (calm). > 1.0 = backwardation."""
        current = current or self.fetch_current()
        if not current or "VIX" not in current or "VIX3M" not in current:
            return None
        vix3m = current["VIX3M"]
        if vix3m <= 0:
            return None
        return round(current["VIX"] / vix3m, 4)

    def is_backwardation(self, current: dict | None = None) -> bool:
        """True if VIX > VIX3M (the kill-switch signal for iron condors)."""
        ratio = self.contango_ratio(current)
        return ratio is not None and ratio > RATIO_CALM_MAX

    def regime_flag(self, current: dict | None = None) -> str:
        """
        Collapse the ratio into one of:
            "calm"           ratio <= 1.00
            "cautious"       1.00 < ratio <= 1.10
            "stress"         1.10 < ratio <= 1.20
            "extreme_stress" ratio > 1.20
            "unknown"        data unavailable
        """
        ratio = self.contango_ratio(current)
        if ratio is None:
            return "unknown"
        if ratio <= RATIO_CALM_MAX:     return "calm"
        if ratio <= RATIO_CAUTIOUS_MAX: return "cautious"
        if ratio <= RATIO_STRESS_MAX:   return "stress"
        return "extreme_stress"

    def snapshot(self) -> dict:
        """
        One-call summary suitable for KB entries, dashboard, and Discord:

            {
                "VIX9D":    13.2,
                "VIX":      14.1,
                "VIX3M":    15.0,
                "VIX6M":    16.2,
                "ratio":    0.94,
                "flag":     "calm",
                "asof":     "2026-05-16T20:00:00",
            }

        If data is unavailable, returns the same shape with None values
        and flag = "unknown" so consumers can render a degraded view.
        """
        current = self.fetch_current() or {}
        ratio   = self.contango_ratio(current) if current else None
        flag    = self.regime_flag(current)
        return {
            "VIX9D": current.get("VIX9D"),
            "VIX":   current.get("VIX"),
            "VIX3M": current.get("VIX3M"),
            "VIX6M": current.get("VIX6M"),
            "ratio": ratio,
            "flag":  flag,
            "asof":  datetime.now().isoformat(timespec="seconds"),
        }

    # ── PRIVATE: CBOE fetch ────────────────────────────────

    @staticmethod
    def _fetch_latest_close(url: str) -> Optional[float]:
        """Download one CBOE CSV and return the latest close as float."""
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = [c.strip().lower() for c in df.columns]
            if "close" not in df.columns:
                logger.warning(f"VIXTermStructure: no 'close' column in {url}")
                return None
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            if close.empty:
                return None
            return float(close.iloc[-1])
        except Exception as e:
            logger.warning(f"VIXTermStructure CBOE fetch failed ({url}): {e}")
            return None


# ─────────────────────────────────────────
# STANDALONE SMOKE TEST
# ─────────────────────────────────────────

if __name__ == "__main__":
    ts = VIXTermStructure()
    snap = ts.snapshot()
    print("\n── VIX TERM STRUCTURE ──")
    for k, v in snap.items():
        print(f"  {k:<8} {v}")
