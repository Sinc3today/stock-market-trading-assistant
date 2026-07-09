"""tests/test_fmt.py -- user-facing date/time formatting (mm-dd-yy, 12-hour).

Storage stays ISO (parsers/sorters/idempotency depend on it); these helpers
convert at the DISPLAY edge only.
"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts.fmt import fmt_date, fmt_dt, parse_date_flex


def test_fmt_date_from_iso_string():
    assert fmt_date("2026-07-24") == "07-24-26"
    assert fmt_date("2026-07-09T14:05:00") == "07-09-26"


def test_fmt_date_from_objects():
    assert fmt_date(date(2026, 7, 24)) == "07-24-26"
    assert fmt_date(datetime(2026, 7, 24, 9, 30)) == "07-24-26"


def test_fmt_date_passthrough_on_garbage():
    assert fmt_date("1-3DTE") == "1-3DTE"       # dte buckets flow through _exp()
    assert fmt_date(None) == "—"
    assert fmt_date("") == "—"


def test_fmt_dt_journal_stamp():
    # journal entry_date format: "2026-07-09 12:50 AM EST"
    assert fmt_dt("2026-07-09 12:50 AM EST") == "07-09-26 12:50 AM"


def test_fmt_dt_iso_datetime():
    assert fmt_dt("2026-07-09T14:05:33") == "07-09-26 2:05 PM"
    assert fmt_dt(datetime(2026, 7, 9, 9, 45)) == "07-09-26 9:45 AM"


def test_fmt_dt_date_only_falls_back_to_date():
    assert fmt_dt("2026-07-09") == "07-09-26"


def test_parse_date_flex_accepts_both_directions():
    assert parse_date_flex("2026-07-24") == "2026-07-24"     # ISO in -> ISO out
    assert parse_date_flex("07-24-26") == "2026-07-24"       # mm-dd-yy -> ISO
    assert parse_date_flex("07/24/2026") == "2026-07-24"     # slashes tolerated
    assert parse_date_flex("") is None
    assert parse_date_flex("garbage") is None
