"""tests/test_play_leg_formatting.py -- plays show per-leg entry price + expiration
so they can be copied into a live broker."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.options_layer import OptionsLayer


def test_leg_line_includes_strike_and_entry_price():
    leg = {"action": "sell", "option_type": "put", "strike": 739.0, "mid": 2.15,
           "note": "Sell OTM Put"}
    line = OptionsLayer._format_leg_line(leg)
    assert "SELL" in line
    assert "put" in line
    assert "$739" in line
    assert "$2.15" in line          # the per-leg entry premium


def test_leg_line_falls_back_to_mark_when_mid_is_none():
    # the live case: Polygon snapshot has no quotes -> mid None, mark = day close
    leg = {"action": "buy", "option_type": "call", "strike": 760.0,
           "mid": None, "mark": 0.95, "note": "Buy wing"}
    line = OptionsLayer._format_leg_line(leg)
    assert "$760" in line
    assert "$0.95" in line           # mark used as the entry price


def test_leg_line_handles_no_price_at_all_gracefully():
    leg = {"action": "buy", "option_type": "call", "strike": 760.0,
           "mid": None, "mark": None, "note": "Buy wing"}
    line = OptionsLayer._format_leg_line(leg)
    assert "$760" in line
    assert "@ $" not in line         # no bogus price when neither is available


def test_leg_line_falls_back_to_note_without_strike():
    leg = {"action": "buy", "note": "theoretical long call (no chain)"}
    line = OptionsLayer._format_leg_line(leg)
    assert "theoretical long call" in line


def test_expiration_line_shows_real_date():
    """This test used to assert that the REQUESTED dte (45) appeared next to
    the chosen expiry. That is the defect, not the spec: across six briefs in
    Aug/Sep 2026 the line read "(45 days)" while holding contracts 38 to 50
    days out. The date is measured; the requested dte is an intention. Assert
    the measured distance.

    Uses a relative date so the expected number stays meaningful over time —
    the old hardcoded 2026-07-17 silently became a date in the past.
    """
    from datetime import date, timedelta
    today = date(2026, 9, 8)
    exp = today + timedelta(days=38)
    legs = [{"expiration": exp.isoformat(), "strike": 739},
            {"expiration": exp.isoformat(), "strike": 734}]
    line = OptionsLayer._expiration_line(legs, dte=45, today=today)
    assert exp.isoformat() in line
    assert "38 days" in line
    assert "(45 days)" not in line


def test_expiration_line_falls_back_to_dte_when_no_date():
    line = OptionsLayer._expiration_line([{"strike": 739}], dte=45)
    assert "45" in line
