"""
data/event_calendar.py — Economic Event Calendar

Tracks high-impact dates that cause the RegimeDetector to skip trading:
    - FOMC decisions (Fed rate decisions)
    - CPI releases (Consumer Price Index)
    - NFP releases (Non-Farm Payrolls — first Friday of each month)
    - Monthly options expiration — OPEX (third Friday of each month)

Strategy:
    Primary:  Fetch from the Fed's own website (FOMC) + computed dates (NFP/OPEX)
    Fallback: Static 12-month rolling list that covers 99% of cases
    Cache:    Written to logs/event_calendar.json, refreshed weekly

Why this matters:
    SPY can move 1-3% on FOMC/CPI days regardless of technicals.
    The regime classifier is built for trending/choppy normal markets —
    it has no edge on event-driven moves. Skipping these days
    is not being conservative, it's being correct.

Usage:
    from data.event_calendar import EventCalendar
    cal = EventCalendar()
    dates = cal.get_block_dates()           # list[date] — pass to RegimeDetector
    cal.is_event_day()                      # bool — is today blocked?
    cal.get_next_events(days=14)            # upcoming events in the next N days

Run standalone to preview upcoming events:
    python -m data.event_calendar
"""

from __future__ import annotations

import os
import sys
import json
import re
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

# ── Cache settings ────────────────────────────────────────────
CACHE_TTL_DAYS = 7   # Refresh once a week

def _cache_file() -> str:
    """Resolved at call-time so monkeypatched LOG_DIR works in tests."""
    return os.path.join(config.LOG_DIR, "event_calendar.json")

# ── Fed calendar URL (plain HTML, no auth needed) ─────────────
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


class EventCalendar:
    """
    Maintains a list of high-impact economic event dates.
    Auto-refreshes weekly from public sources.
    Falls back to computed dates if fetch fails.
    """

    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)

    # ─────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────

    def get_block_dates(self, months_ahead: int = 6) -> list[date]:
        """
        Return all event dates for the next N months.
        This is what you pass to RegimeDetector(event_calendar=...).

        Returns list[date], deduplicated and sorted.
        """
        cached = self._load_cache()

        if cached and self._cache_is_fresh(cached):
            dates = self._parse_cache(cached)
            logger.debug(f"Event calendar from cache: {len(dates)} dates")
            return dates

        # Build fresh calendar
        events = self._build_calendar(months_ahead)
        self._save_cache(events)
        dates = [date.fromisoformat(e["date"]) if isinstance(e["date"], str)
                 else e["date"] for e in events]
        today = date.today()
        dates = [d for d in dates if d >= today]
        logger.info(f"Event calendar refreshed: {len(dates)} dates in next {months_ahead} months")
        return sorted(set(dates))

    def is_event_day(self, check_date: date | None = None) -> bool:
        """Return True if today (or check_date) is a blocked event day."""
        check_date = check_date or date.today()
        return check_date in set(self.get_block_dates())

    def get_next_events(self, days: int = 14) -> list[dict]:
        """
        Return upcoming events within the next N days.
        Each item: {"date": date, "type": str, "label": str}
        """
        today   = date.today()
        cutoff  = today + timedelta(days=days)
        cached  = self._load_cache()

        if cached and self._cache_is_fresh(cached):
            events = cached.get("events", [])
        else:
            events = self._build_calendar(months_ahead=3)
            self._save_cache({"events": events, "built_at": date.today().isoformat()})

        upcoming = []
        for e in events:
            d = date.fromisoformat(e["date"]) if isinstance(e["date"], str) else e["date"]
            if today <= d <= cutoff:
                upcoming.append({
                    "date":  d,
                    "type":  e.get("type", "unknown"),
                    "label": e.get("label", e.get("type", "")),
                    "days_away": (d - today).days,
                })

        return sorted(upcoming, key=lambda x: x["date"])

    # ─────────────────────────────────────────
    # CALENDAR BUILDER
    # ─────────────────────────────────────────

    def _build_calendar(self, months_ahead: int = 6) -> list[dict]:
        """
        Build the complete event list from all sources.
        Returns list of {"date": str (ISO), "type": str, "label": str}
        """
        events: list[dict] = []

        # 1. NFP — first Friday of each month (always reliable, computed)
        events += self._compute_nfp_dates(months_ahead)

        # 2. OPEX — third Friday of each month (computed)
        events += self._compute_opex_dates(months_ahead)

        # 3. FOMC — fetch from Fed website, fallback to known schedule
        events += self._fetch_fomc_dates() or self._static_fomc_dates()

        # 4. CPI — typically 2nd or 3rd Tuesday/Wednesday; use static schedule
        events += self._static_cpi_dates()

        # Deduplicate by date+type, sort by date
        seen = set()
        deduped = []
        for e in events:
            key = (e["date"], e["type"])
            if key not in seen:
                seen.add(key)
                deduped.append(e)

        return sorted(deduped, key=lambda x: x["date"])

    # ─────────────────────────────────────────
    # NFP — First Friday of each month
    # ─────────────────────────────────────────

    @staticmethod
    def _compute_nfp_dates(months_ahead: int) -> list[dict]:
        """
        NFP (Non-Farm Payrolls) is released on the first Friday of each month.
        The actual release is at 8:30 AM ET — markets move before open.
        We block the entire day.
        """
        events = []
        today  = date.today()
        for m in range(months_ahead + 1):
            year  = (today.replace(day=1) + timedelta(days=32 * m)).year
            month = (today.replace(day=1) + timedelta(days=32 * m)).month
            # Find first Friday
            first_day = date(year, month, 1)
            days_to_friday = (4 - first_day.weekday()) % 7
            nfp_date = first_day + timedelta(days=days_to_friday)
            if nfp_date >= today:
                events.append({
                    "date":  nfp_date.isoformat(),
                    "type":  "NFP",
                    "label": f"NFP — {nfp_date.strftime('%b %Y')}",
                })
        return events

    # ─────────────────────────────────────────
    # OPEX — Third Friday of each month
    # ─────────────────────────────────────────

    @staticmethod
    def _compute_opex_dates(months_ahead: int) -> list[dict]:
        """
        Monthly options expiration (OPEX) = third Friday of each month.
        SPY and QQQ have massive open interest — gamma unwinding moves markets.
        Block the day and the day before for major quarterly OPEX (Mar/Jun/Sep/Dec).
        """
        events = []
        today  = date.today()
        for m in range(months_ahead + 1):
            year  = (today.replace(day=1) + timedelta(days=32 * m)).year
            month = (today.replace(day=1) + timedelta(days=32 * m)).month

            # Third Friday
            first_day = date(year, month, 1)
            days_to_friday = (4 - first_day.weekday()) % 7
            first_friday   = first_day + timedelta(days=days_to_friday)
            opex_date      = first_friday + timedelta(weeks=2)

            if opex_date >= today:
                is_quarterly = month in (3, 6, 9, 12)
                label_suffix = " (quarterly)" if is_quarterly else ""
                events.append({
                    "date":  opex_date.isoformat(),
                    "type":  "OPEX",
                    "label": f"OPEX{label_suffix} — {opex_date.strftime('%b %Y')}",
                })
                # Also block day before quarterly OPEX (Thursday) —
                # dealers hedge Thursday afternoon before Friday expiry
                if is_quarterly:
                    day_before = opex_date - timedelta(days=1)
                    events.append({
                        "date":  day_before.isoformat(),
                        "type":  "OPEX_EVE",
                        "label": f"OPEX eve (quarterly) — {day_before.strftime('%b %Y')}",
                    })
        return events

    # ─────────────────────────────────────────
    # FOMC — Fetch from Fed website
    # ─────────────────────────────────────────

    def _fetch_fomc_dates(self) -> list[dict] | None:
        """
        Scrape FOMC meeting dates from the Federal Reserve website.
        Returns None if fetch fails (fallback to static list).

        The Fed page has a simple pattern:
            "January 28-29" or "March 18-19*" (* = press conference)
        We parse out the decision day (second day of each meeting).
        """
        try:
            resp = requests.get(FED_CALENDAR_URL, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            html  = resp.text
            today = date.today()
            year  = today.year

            events = []
            months = {
                "January":1,"February":2,"March":3,"April":4,
                "May":5,"June":6,"July":7,"August":8,
                "September":9,"October":10,"November":11,"December":12,
            }

            # Find all year sections and date ranges
            # Pattern: "Month DD-DD" or "Month DD-\nMonth DD"
            pattern = re.compile(
                r'(January|February|March|April|May|June|July|'
                r'August|September|October|November|December)'
                r'\s+(\d{1,2})(?:[–\-](?:\w+\s+)?(\d{1,2}))?'
            )

            for y in range(year, year + 2):
                # Look for the year heading, then parse dates after it
                year_pattern = re.compile(rf'<h\d[^>]*>\s*{y}\s*</h\d>', re.IGNORECASE)
                year_match = year_pattern.search(html)
                if not year_match:
                    continue

                # Get text from this year heading to the next
                next_year_match = re.compile(rf'<h\d[^>]*>\s*{y+1}\s*</h\d>', re.IGNORECASE).search(html, year_match.end())
                end_pos = next_year_match.start() if next_year_match else len(html)
                section = html[year_match.start():end_pos]

                for match in pattern.finditer(section):
                    month_name = match.group(1)
                    day1       = int(match.group(2))
                    day2       = int(match.group(3)) if match.group(3) else day1
                    month_num  = months[month_name]

                    # Decision day = last day of meeting
                    try:
                        decision_date = date(y, month_num, day2)
                        if decision_date >= today:
                            events.append({
                                "date":  decision_date.isoformat(),
                                "type":  "FOMC",
                                "label": f"FOMC decision — {decision_date.strftime('%b %d, %Y')}",
                            })
                            # Also block the day before (markets price in expected moves)
                            day_before = decision_date - timedelta(days=1)
                            events.append({
                                "date":  day_before.isoformat(),
                                "type":  "FOMC_EVE",
                                "label": f"FOMC eve — {day_before.strftime('%b %d, %Y')}",
                            })
                    except ValueError:
                        continue  # Bad date, skip

            if events:
                logger.info(f"Fetched {len(events)} FOMC dates from Fed website")
                return events

        except Exception as e:
            logger.warning(f"Fed calendar fetch failed: {e} — using static FOMC list")
        return None

    # ─────────────────────────────────────────
    # STATIC FOMC — 2025-2026 known schedule
    # ─────────────────────────────────────────

    @staticmethod
    def _static_fomc_dates() -> list[dict]:
        """
        Static FOMC decision dates for 2025-2026.
        Updated annually — the Fed publishes the schedule a year in advance.
        Add FOMC_EVE (day before) for each.
        """
        decision_days = [
            # 2025
            date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
            date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
            date(2025, 10, 29), date(2025, 12, 10),
            # 2026
            date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
            date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
            date(2026, 10, 28), date(2026, 12, 9),
        ]
        today  = date.today()
        events = []
        for d in decision_days:
            if d >= today:
                events.append({
                    "date":  d.isoformat(),
                    "type":  "FOMC",
                    "label": f"FOMC decision — {d.strftime('%b %d, %Y')}",
                })
                day_before = d - timedelta(days=1)
                events.append({
                    "date":  day_before.isoformat(),
                    "type":  "FOMC_EVE",
                    "label": f"FOMC eve — {day_before.strftime('%b %d, %Y')}",
                })
        return events

    # ─────────────────────────────────────────
    # STATIC CPI — BLS releases
    # ─────────────────────────────────────────

    @staticmethod
    def _static_cpi_dates() -> list[dict]:
        """
        CPI release dates for 2025-2026 (Bureau of Labor Statistics).
        Released at 8:30 AM ET, typically 2nd or 3rd Wed/Thu of the month.
        The BLS publishes the full year's schedule in January each year.
        Update annually from: https://www.bls.gov/schedule/news_release/cpi.htm
        """
        cpi_days = [
            # 2025
            date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12),
            date(2025, 4, 10), date(2025, 5, 13), date(2025, 6, 11),
            date(2025, 7, 15), date(2025, 8, 12), date(2025, 9, 10),
            date(2025, 10, 15), date(2025, 11, 13), date(2025, 12, 10),
            # 2026
            date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11),
            date(2026, 4, 10), date(2026, 5, 12), date(2026, 6, 10),
            date(2026, 7, 14), date(2026, 8, 11), date(2026, 9, 9),
            date(2026, 10, 13), date(2026, 11, 10), date(2026, 12, 8),
        ]
        today  = date.today()
        events = []
        for d in cpi_days:
            if d >= today:
                events.append({
                    "date":  d.isoformat(),
                    "type":  "CPI",
                    "label": f"CPI release — {d.strftime('%b %d, %Y')}",
                })
        return events

    # ─────────────────────────────────────────
    # CACHE
    # ─────────────────────────────────────────

    def _load_cache(self) -> dict | None:
        if not os.path.exists(_cache_file()):
            return None
        try:
            with open(_cache_file()) as f:
                return json.load(f)
        except Exception:
            return None

    def _save_cache(self, data: dict | list):
        payload = {
            "built_at": date.today().isoformat(),
            "events":   data if isinstance(data, list) else data.get("events", []),
        }
        try:
            with open(_cache_file(), "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save event calendar cache: {e}")

    def _cache_is_fresh(self, cached: dict) -> bool:
        built_str = cached.get("built_at")
        if not built_str:
            return False
        built = date.fromisoformat(built_str)
        return (date.today() - built).days < CACHE_TTL_DAYS

    @staticmethod
    def _parse_cache(cached: dict) -> list[date]:
        events = cached.get("events", [])
        today  = date.today()
        dates  = []
        for e in events:
            try:
                d = date.fromisoformat(e["date"])
                if d >= today:
                    dates.append(d)
            except (KeyError, ValueError):
                continue
        return sorted(set(dates))


# ─────────────────────────────────────────
# STANDALONE PREVIEW
# ─────────────────────────────────────────

if __name__ == "__main__":
    cal = EventCalendar()

    print("\n── Upcoming events (next 60 days) ──")
    upcoming = cal.get_next_events(days=60)
    if not upcoming:
        print("  No events in the next 60 days")
    for e in upcoming:
        marker = "🔴 TODAY" if e["days_away"] == 0 else f"  {e['days_away']:>2}d away"
        print(f"  {marker}  {e['date']}  {e['label']}")

    print(f"\n── Is today ({date.today()}) a block day? ──")
    print(f"  {cal.is_event_day()}")

    all_dates = cal.get_block_dates()
    print(f"\n── Total block dates (next 6 months): {len(all_dates)} ──")
    for t in ["FOMC", "FOMC_EVE", "CPI", "NFP", "OPEX", "OPEX_EVE"]:
        cached = cal._load_cache()
        if cached:
            count = sum(1 for e in cached.get("events", []) if e.get("type") == t)
            print(f"  {t:<12} {count}")
