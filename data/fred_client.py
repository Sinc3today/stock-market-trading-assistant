"""
data/fred_client.py — FRED Economic Data Client
Fetches economic series data and release calendars from FRED API.

Key series tracked:
    CPIAUCSL  — Consumer Price Index (CPI)
    PAYEMS    — Non-Farm Payrolls
    FEDFUNDS  — Federal Funds Rate
    GDP       — Gross Domestic Product
    UNRATE    — Unemployment Rate
    PCE       — Personal Consumption Expenditures
    ISM       — ISM Manufacturing PMI (via MANEMP proxy)

Usage:
    from data.fred_client import FREDClient
    client = FREDClient()
    data = client.get_latest_observation("CPIAUCSL")
"""

import os
import time
import requests
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
import config


# ── Key economic series we track ─────────────────────────────
TRACKED_SERIES = {
    "CPIAUCSL": {
        "name":       "CPI (Inflation)",
        "short":      "CPI",
        "impact":     "HIGH",
        "frequency":  "monthly",
        "unit":       "Index",
        "emoji":      "📊",
        "description": "Consumer Price Index — measures inflation",
    },
    "PAYEMS": {
        "name":       "Non-Farm Payrolls",
        "short":      "NFP",
        "impact":     "HIGH",
        "frequency":  "monthly",
        "unit":       "Thousands",
        "emoji":      "👷",
        "description": "Total jobs added/lost in the economy",
    },
    "FEDFUNDS": {
        "name":       "Federal Funds Rate",
        "short":      "Fed Rate",
        "impact":     "HIGH",
        "frequency":  "monthly",
        "unit":       "Percent",
        "emoji":      "🏦",
        "description": "Federal Reserve interest rate target",
    },
    "UNRATE": {
        "name":       "Unemployment Rate",
        "short":      "Unemployment",
        "impact":     "HIGH",
        "frequency":  "monthly",
        "unit":       "Percent",
        "emoji":      "📉",
        "description": "Percentage of labor force unemployed",
    },
    "GDP": {
        "name":       "GDP Growth",
        "short":      "GDP",
        "impact":     "HIGH",
        "frequency":  "quarterly",
        "unit":       "Billions USD",
        "emoji":      "🏛️",
        "description": "Gross Domestic Product — overall economic output",
    },
    "PCEPI": {
        "name":       "PCE Inflation",
        "short":      "PCE",
        "impact":     "HIGH",
        "frequency":  "monthly",
        "unit":       "Index",
        "emoji":      "💰",
        "description": "Fed's preferred inflation measure",
    },
    "T10Y2Y": {
        "name":       "Yield Curve (10Y-2Y)",
        "short":      "Yield Curve",
        "impact":     "MEDIUM",
        "frequency":  "daily",
        "unit":       "Percent",
        "emoji":      "📈",
        "description": "Spread between 10yr and 2yr Treasury — recession indicator",
    },
    "UMCSENT": {
        "name":       "Consumer Sentiment",
        "short":      "Consumer Sentiment",
        "impact":     "MEDIUM",
        "frequency":  "monthly",
        "unit":       "Index",
        "emoji":      "😊",
        "description": "University of Michigan Consumer Sentiment Index",
    },
}

# High impact series that trigger immediate Discord alerts
HIGH_IMPACT_SERIES = [s for s, d in TRACKED_SERIES.items() if d["impact"] == "HIGH"]


class FREDClient:
    """
    Client for FRED (Federal Reserve Economic Data) API.
    Fetches observations, tracks changes, and identifies surprises.
    """

    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self):
        self.api_key = getattr(config, "FRED_API_KEY", None) or os.getenv("FRED_API_KEY")
        if not self.api_key:
            raise ValueError("FRED_API_KEY not set in config or .env")
        logger.info("FREDClient initialized")

    # ─────────────────────────────────────────
    # LATEST OBSERVATION
    # ─────────────────────────────────────────

    def get_latest_observation(
        self,
        series_id: str,
        num_observations: int = 3,
    ) -> Optional[dict]:
        """
        Get the most recent observations for a series.
        Returns current, previous, and change for context.

        Args:
            series_id:        FRED series ID e.g. "CPIAUCSL"
            num_observations: How many recent values to return

        Returns:
            dict with current, previous, change, date, metadata
        """
        try:
            url    = f"{self.BASE_URL}/series/observations"
            params = {
                "series_id":  series_id,
                "api_key":    self.api_key,
                "file_type":  "json",
                "limit":      num_observations,
                "sort_order": "desc",
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            obs  = data.get("observations", [])

            if not obs:
                logger.warning(f"No observations for {series_id}")
                return None

            current  = obs[0]
            previous = obs[1] if len(obs) > 1 else None

            # Calculate change
            change     = None
            change_pct = None
            if previous and current["value"] != "." and previous["value"] != ".":
                try:
                    curr_val = float(current["value"])
                    prev_val = float(previous["value"])
                    change   = round(curr_val - prev_val, 4)
                    change_pct = round((change / abs(prev_val)) * 100, 2) \
                                 if prev_val != 0 else None
                except (ValueError, ZeroDivisionError):
                    pass

            meta = TRACKED_SERIES.get(series_id, {})

            return {
                "series_id":   series_id,
                "name":        meta.get("name", series_id),
                "short":       meta.get("short", series_id),
                "emoji":       meta.get("emoji", "📊"),
                "impact":      meta.get("impact", "MEDIUM"),
                "unit":        meta.get("unit", ""),
                "description": meta.get("description", ""),
                "current_date":  current["date"],
                "current_value": current["value"],
                "previous_date": previous["date"] if previous else None,
                "previous_value":previous["value"] if previous else None,
                "change":        change,
                "change_pct":    change_pct,
                "direction":     "up" if change and change > 0 else
                                 "down" if change and change < 0 else "flat",
            }

        except Exception as e:
            logger.error(f"FRED observation error for {series_id}: {type(e).__name__}")
            return None

    # ─────────────────────────────────────────
    # ALL TRACKED SERIES
    # ─────────────────────────────────────────

    def get_all_tracked(self) -> dict:
        """
        Fetch latest data for all tracked economic series.
        Returns dict keyed by series_id.
        """
        results = {}
        for series_id in TRACKED_SERIES:
            time.sleep(0.5)  # Gentle rate limiting
            data = self.get_latest_observation(series_id)
            if data:
                results[series_id] = data
                logger.debug(
                    f"FRED {series_id}: {data['current_value']} "
                    f"({data['current_date']})"
                )
        logger.info(f"FRED: Fetched {len(results)}/{len(TRACKED_SERIES)} series")
        return results

    # ─────────────────────────────────────────
    # RECENT RELEASES (what just came out)
    # ─────────────────────────────────────────

    def get_recent_releases(self, days_back: int = 3) -> list:
        """
        Find series that have been updated in the last N days.
        These are the reports that just came out.
        """
        cutoff   = datetime.utcnow() - timedelta(days=days_back)
        released = []

        all_data = self.get_all_tracked()
        for series_id, data in all_data.items():
            try:
                release_date = datetime.strptime(data["current_date"], "%Y-%m-%d")
                if release_date >= cutoff:
                    released.append(data)
                    logger.info(
                        f"Recent release: {data['name']} on "
                        f"{data['current_date']} = {data['current_value']}"
                    )
            except Exception:
                continue

        released.sort(key=lambda x: x["current_date"], reverse=True)
        return released

    # ─────────────────────────────────────────
    # ECONOMIC SNAPSHOT
    # ─────────────────────────────────────────

    def get_economic_snapshot(self) -> dict:
        """
        Build a complete economic snapshot for the AI advisor.
        Shows current state of all key indicators.
        """
        all_data = self.get_all_tracked()

        snapshot = {
            "timestamp":  datetime.utcnow().strftime("%Y-%m-%d"),
            "indicators": all_data,
            "high_impact": {
                k: v for k, v in all_data.items()
                if v.get("impact") == "HIGH"
            },
            "recent_releases": self.get_recent_releases(days_back=7),
            "summary": self._build_summary(all_data),
        }
        return snapshot

    def _build_summary(self, all_data: dict) -> str:
        """Build a plain text summary of current economic conditions."""
        lines = []
        for series_id, data in all_data.items():
            if data.get("impact") == "HIGH":
                val  = data["current_value"]
                date = data["current_date"]
                chg  = f" ({data['change']:+.3f})" if data.get("change") else ""
                lines.append(
                    f"{data['emoji']} {data['short']}: "
                    f"{val} {data['unit']}{chg} as of {date}"
                )
        return "\n".join(lines)
