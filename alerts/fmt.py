"""alerts/fmt.py -- user-facing date/time formatting.

House style (user, 2026-07-10): dates as MM-DD-YY, times as 12-hour h:mm AM/PM.

STORAGE STAYS ISO. The journal, predictions, plans, and sync logic parse, sort,
and prefix-compare ISO strings — these helpers convert at the DISPLAY edge only
(web pages, notifications). parse_date_flex() is the inverse for form inputs,
accepting the display format (or ISO) and normalizing back to ISO for storage.

All helpers are defensive: anything unparseable passes through unchanged (or
returns the em-dash placeholder) rather than raising in a render path.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_DASH = "—"

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TIME_12H = re.compile(r"\b(\d{1,2}):(\d{2})\s*(AM|PM)\b", re.IGNORECASE)
_TIME_24H = re.compile(r"[T ](\d{2}):(\d{2})")
_MDY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")


def fmt_date(v) -> str:
    """MM-DD-YY for dates/datetimes/ISO-ish strings; passthrough otherwise."""
    if v is None or v == "":
        return _DASH
    if isinstance(v, (date, datetime)):
        return v.strftime("%m-%d-%y")
    m = _ISO_DATE.search(str(v))
    if not m:
        return str(v)
    y, mo, d = m.groups()
    return f"{mo}-{d}-{y[2:]}"


def fmt_dt(v) -> str:
    """MM-DD-YY h:mm AM/PM. Handles the journal stamp ('2026-07-09 12:50 AM EST'),
    ISO datetimes, and date/datetime objects; date-only input degrades to fmt_date."""
    if v is None or v == "":
        return _DASH
    if isinstance(v, datetime):
        return f"{v.strftime('%m-%d-%y')} {v.strftime('%I:%M %p').lstrip('0')}"
    if isinstance(v, date):
        return fmt_date(v)
    s = str(v)
    d = fmt_date(s)
    if d == s:                      # no ISO date found — passthrough
        return s
    m = _TIME_12H.search(s)
    if m:
        h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
        return f"{d} {h}:{mi} {ap}"
    m = _TIME_24H.search(s)
    if m:
        h, mi = int(m.group(1)), m.group(2)
        ap = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{d} {h12}:{mi} {ap}"
    return d


def parse_date_flex(s) -> str | None:
    """Form-input inverse: accept MM-DD-YY, MM/DD/YYYY, or ISO; return ISO
    (YYYY-MM-DD) for storage. None if unparseable."""
    if not s:
        return None
    s = str(s).strip()
    m = _ISO_DATE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _MDY.match(s)
    if not m:
        return None
    mo, d, y = (int(m.group(1)), int(m.group(2)), m.group(3))
    year = int(y) + 2000 if len(y) == 2 else int(y)
    try:
        return date(year, mo, d).isoformat()
    except ValueError:
        return None
