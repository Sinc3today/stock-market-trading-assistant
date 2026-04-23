"""
tests/test_event_calendar.py — Test EventCalendar

All tests are unit tests — no live network calls.
The Fed website fetch is mocked; computed dates (NFP, OPEX) are validated
against known calendar facts.

Run with:
    pytest tests/test_event_calendar.py -v
"""

import pytest
import sys
import os
import json
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data.event_calendar import EventCalendar


# ─────────────────────────────────────────
# FIXTURE
# ─────────────────────────────────────────

@pytest.fixture
def cal(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return EventCalendar()


# ─────────────────────────────────────────
# NFP DATE COMPUTATION
# ─────────────────────────────────────────

class TestNFPDates:

    def test_nfp_is_always_a_friday(self, cal):
        events = cal._compute_nfp_dates(months_ahead=6)
        for e in events:
            d = date.fromisoformat(e["date"])
            assert d.weekday() == 4, f"NFP {d} is not a Friday (weekday={d.weekday()})"
        print(f"\n✅ All {len(events)} NFP dates are Fridays")

    def test_nfp_is_first_friday(self, cal):
        """First Friday = day 1-7 of the month."""
        events = cal._compute_nfp_dates(months_ahead=12)
        for e in events:
            d = date.fromisoformat(e["date"])
            assert 1 <= d.day <= 7, f"NFP {d} is not in first 7 days (day={d.day})"
        print(f"\n✅ All {len(events)} NFP dates fall in days 1–7")

    def test_nfp_type_label(self, cal):
        events = cal._compute_nfp_dates(months_ahead=3)
        for e in events:
            assert e["type"] == "NFP"
            assert "NFP" in e["label"]
        print("\n✅ NFP type and label correct")


# ─────────────────────────────────────────
# OPEX DATE COMPUTATION
# ─────────────────────────────────────────

class TestOPEXDates:

    def test_opex_is_always_a_friday(self, cal):
        events = [e for e in cal._compute_opex_dates(months_ahead=12)
                  if e["type"] == "OPEX"]
        for e in events:
            d = date.fromisoformat(e["date"])
            assert d.weekday() == 4, f"OPEX {d} is not a Friday"
        print(f"\n✅ All {len(events)} OPEX dates are Fridays")

    def test_opex_is_third_friday(self, cal):
        """Third Friday = day 15-21 of the month."""
        events = [e for e in cal._compute_opex_dates(months_ahead=12)
                  if e["type"] == "OPEX"]
        for e in events:
            d = date.fromisoformat(e["date"])
            assert 15 <= d.day <= 21, f"OPEX {d} not in days 15–21 (day={d.day})"
        print(f"\n✅ All {len(events)} OPEX dates fall in days 15–21")

    def test_quarterly_opex_has_eve(self, cal):
        """March/June/Sep/Dec OPEX should have a matching OPEX_EVE the day before."""
        events = cal._compute_opex_dates(months_ahead=12)
        opex_dates  = {date.fromisoformat(e["date"]) for e in events if e["type"] == "OPEX"
                       and date.fromisoformat(e["date"]).month in (3,6,9,12)}
        eve_dates   = {date.fromisoformat(e["date"]) for e in events if e["type"] == "OPEX_EVE"}
        expected_eves = {d - timedelta(days=1) for d in opex_dates}
        assert expected_eves == eve_dates, \
            f"Missing OPEX eves: {expected_eves - eve_dates}"
        print(f"\n✅ All {len(eve_dates)} quarterly OPEX eves present")

    def test_non_quarterly_opex_has_no_eve(self, cal):
        """Non-quarterly months should NOT have OPEX_EVE."""
        events     = cal._compute_opex_dates(months_ahead=12)
        opex_dates = {date.fromisoformat(e["date"]) for e in events
                      if e["type"] == "OPEX"
                      and date.fromisoformat(e["date"]).month not in (3,6,9,12)}
        eve_dates  = {date.fromisoformat(e["date"]) for e in events if e["type"] == "OPEX_EVE"}
        bleed = {d - timedelta(days=1) for d in opex_dates} & eve_dates
        assert not bleed, f"Non-quarterly months have OPEX_EVE: {bleed}"
        print(f"\n✅ Non-quarterly months have no OPEX_EVE")


# ─────────────────────────────────────────
# STATIC SCHEDULES
# ─────────────────────────────────────────

class TestStaticSchedules:

    def test_static_fomc_dates_are_valid(self, cal):
        events = cal._static_fomc_dates()
        fomc_only = [e for e in events if e["type"] == "FOMC"]
        assert len(fomc_only) >= 4, "Expected at least 4 upcoming FOMC dates"
        for e in fomc_only:
            d = date.fromisoformat(e["date"])
            assert d.weekday() < 5, f"FOMC {d} falls on a weekend"
        print(f"\n✅ {len(fomc_only)} static FOMC dates, all weekdays")

    def test_static_fomc_has_eves(self, cal):
        events  = cal._static_fomc_dates()
        fomc    = {date.fromisoformat(e["date"]) for e in events if e["type"] == "FOMC"}
        eves    = {date.fromisoformat(e["date"]) for e in events if e["type"] == "FOMC_EVE"}
        expected = {d - timedelta(days=1) for d in fomc}
        assert expected == eves
        print(f"\n✅ All {len(eves)} FOMC eves present")

    def test_static_cpi_dates_are_valid(self, cal):
        events = cal._static_cpi_dates()
        assert len(events) >= 4
        for e in events:
            d = date.fromisoformat(e["date"])
            assert d.weekday() < 5, f"CPI {d} falls on a weekend"
            assert e["type"] == "CPI"
        print(f"\n✅ {len(events)} static CPI dates, all weekdays")

    def test_only_future_dates_returned(self, cal):
        today = date.today()
        for fn in [cal._static_fomc_dates, cal._static_cpi_dates]:
            for e in fn():
                d = date.fromisoformat(e["date"])
                assert d >= today, f"{e['type']} date {d} is in the past"
        print("\n✅ All static dates are today or future")


# ─────────────────────────────────────────
# FOMC FETCH (mocked)
# ─────────────────────────────────────────

class TestFOMCFetch:

    def test_fetch_falls_back_on_network_error(self, cal):
        with patch("requests.get", side_effect=Exception("network error")):
            result = cal._fetch_fomc_dates()
        assert result is None
        print("\n✅ Network error returns None (fallback triggered)")

    def test_fetch_falls_back_on_403(self, cal):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        with patch("requests.get", return_value=mock_resp):
            result = cal._fetch_fomc_dates()
        assert result is None
        print("\n✅ 403 response returns None (fallback triggered)")


# ─────────────────────────────────────────
# FULL CALENDAR
# ─────────────────────────────────────────

class TestFullCalendar:

    def test_get_block_dates_returns_list_of_dates(self, cal):
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            dates = cal.get_block_dates(months_ahead=3)
        assert isinstance(dates, list)
        assert all(isinstance(d, date) for d in dates)
        assert len(dates) > 0
        print(f"\n✅ get_block_dates: {len(dates)} dates returned")

    def test_block_dates_are_sorted(self, cal):
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            dates = cal.get_block_dates(months_ahead=3)
        assert dates == sorted(dates)
        print("\n✅ Block dates are sorted ascending")

    def test_block_dates_are_deduplicated(self, cal):
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            dates = cal.get_block_dates(months_ahead=3)
        assert len(dates) == len(set(dates))
        print(f"\n✅ No duplicate dates in block list")

    def test_all_types_represented(self, cal):
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            cal.get_block_dates(months_ahead=6)
        cached = cal._load_cache()
        types = {e["type"] for e in cached.get("events", [])}
        for expected in ("FOMC", "FOMC_EVE", "CPI", "NFP", "OPEX"):
            assert expected in types, f"Missing event type: {expected}"
        print(f"\n✅ All event types present: {types}")

    def test_is_event_day_known_fomc(self, cal):
        """Inject a known future FOMC date and verify is_event_day detects it."""
        future_fomc = date.today() + timedelta(days=3)
        with patch.object(cal, "get_block_dates", return_value=[future_fomc]):
            assert cal.is_event_day(future_fomc) is True
            assert cal.is_event_day(date.today()) is False
        print(f"\n✅ is_event_day correctly detects injected FOMC date")

    def test_is_event_day_today_not_blocked(self, cal):
        """Normally today should not be a block day (unless it really is)."""
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            result = cal.is_event_day()
        # We can't assert False here since today might actually be an event day
        assert isinstance(result, bool)
        print(f"\n✅ is_event_day(today) returned: {result}")

    def test_get_next_events_structure(self, cal):
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            events = cal.get_next_events(days=60)
        assert isinstance(events, list)
        for e in events:
            assert "date"      in e
            assert "type"      in e
            assert "label"     in e
            assert "days_away" in e
            assert e["days_away"] >= 0
        print(f"\n✅ get_next_events: {len(events)} events, all well-formed")

    def test_get_next_events_sorted_by_date(self, cal):
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            events = cal.get_next_events(days=60)
        dates = [e["date"] for e in events]
        assert dates == sorted(dates)
        print("\n✅ Next events sorted by date")

    def test_cache_written_on_first_call(self, cal):
        import config
        # cal fixture uses tmp_path — no prior cache exists
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            cal.get_block_dates(months_ahead=3)
        from data.event_calendar import _cache_file
        import data.event_calendar as ec_mod
        # temporarily override LOG_DIR path for assertion
        cache_path = os.path.join(config.LOG_DIR, "event_calendar.json")
        assert os.path.exists(cache_path)
        with open(cache_path) as f:
            data = json.load(f)
        assert "built_at" in data
        assert "events"   in data
        print(f"\n✅ Cache written: {len(data['events'])} events")

    def test_cache_is_used_on_second_call(self, cal):
        """Verify second call reads from cache by checking file mtime doesn't change."""
        import config, time
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            cal.get_block_dates(months_ahead=3)  # first call — builds
        cache_path = os.path.join(config.LOG_DIR, "event_calendar.json")
        mtime_after_first = os.path.getmtime(cache_path)
        time.sleep(0.05)
        with patch.object(cal, "_fetch_fomc_dates", return_value=None):
            cal.get_block_dates(months_ahead=3)  # second call — should use cache
        mtime_after_second = os.path.getmtime(cache_path)
        assert mtime_after_first == mtime_after_second, "Cache was rewritten on second call"
        print("\n✅ Cache used on second call (file not rewritten)")
