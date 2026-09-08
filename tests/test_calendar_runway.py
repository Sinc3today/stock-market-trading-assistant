"""tests/test_calendar_runway.py -- hand-curated calendars are dated fuses.

Both of these are static tables someone has to remember to extend, and both
fail SILENTLY and in the dangerous direction:

  * US_MARKET_HOLIDAYS ended 2026-12-25, so from 2027-01-01 is_trading_day()
    returned True on every market holiday — the bot would try to trade a
    closed market, every holiday, forever.
  * The static CPI fallback ended 2026-12-08. CPI has no other source, so
    after that the bot trades into every CPI print with no event-day skip.

The FOMC list had the identical fuse and was extended in 8962ec9; CPI and the
holidays were left behind. These tests fail while there is still runway to fix
it, rather than on the day it breaks.
"""
import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# Enough runway that a quarterly review always catches it before expiry.
MIN_RUNWAY_DAYS = 270


def _future(dates):
    today = date.today()
    return sorted(d for d in dates if d >= today)


def test_market_holidays_have_runway():
    fut = _future(config.US_MARKET_HOLIDAYS)
    assert fut, "no future market holidays at all"
    runway = (max(fut) - date.today()).days
    assert runway >= MIN_RUNWAY_DAYS, (
        f"US_MARKET_HOLIDAYS runs out in {runway} days (last {max(fut)}). "
        "After that is_trading_day() returns True on every holiday.")


def test_known_2027_holidays_are_closed():
    """Spot-check the ones that would otherwise read as tradeable."""
    for y, m, d, label in [(2027, 1, 1, "New Year"), (2027, 1, 18, "MLK"),
                           (2027, 5, 31, "Memorial"), (2027, 7, 5, "Jul4 obs"),
                           (2027, 9, 6, "Labor"), (2027, 11, 25, "Thanksgiving"),
                           (2027, 12, 24, "Christmas obs")]:
        assert not config.is_trading_day(datetime(y, m, d, 12, 0)), \
            f"{label} {y}-{m:02d}-{d:02d} reads as a trading day"


def test_a_normal_2027_weekday_is_still_tradeable():
    """The guard must not over-block."""
    assert config.is_trading_day(datetime(2027, 3, 10, 12, 0))


def test_cpi_dates_have_runway():
    from data.event_calendar import EventCalendar
    rows = EventCalendar()._static_cpi_dates()
    ds = []
    for r in rows:
        d = r.get("date") if isinstance(r, dict) else r
        ds.append(date.fromisoformat(d[:10]) if isinstance(d, str) else d)
    fut = _future(ds)
    assert fut, "no future CPI dates"
    runway = (max(fut) - date.today()).days
    assert runway >= MIN_RUNWAY_DAYS, (
        f"static CPI fallback runs out in {runway} days (last {max(fut)}). "
        "CPI has no other source; after that the bot trades every CPI print "
        "with no event-day skip.")


def test_cpi_dates_are_weekdays():
    """A CPI print never lands on a weekend — a typo here would silently move
    an event-day skip onto the wrong day."""
    from data.event_calendar import EventCalendar
    for r in EventCalendar()._static_cpi_dates():
        d = r.get("date") if isinstance(r, dict) else r
        d = date.fromisoformat(d[:10]) if isinstance(d, str) else d
        assert d.weekday() < 5, f"CPI on a weekend: {d}"


def test_fomc_dates_still_have_runway():
    """The one already fixed — assert it stays fixed."""
    from data.event_calendar import EventCalendar
    ec = EventCalendar()
    rows = ec._static_fomc_dates() if hasattr(ec, "_static_fomc_dates") else []
    ds = []
    for r in rows:
        d = r.get("date") if isinstance(r, dict) else r
        ds.append(date.fromisoformat(d[:10]) if isinstance(d, str) else d)
    fut = _future(ds)
    assert len(fut) >= 4, f"only {len(fut)} future FOMC dates left"
