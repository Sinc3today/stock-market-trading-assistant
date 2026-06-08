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
    legs = [{"expiration": "2026-07-17", "strike": 739},
            {"expiration": "2026-07-17", "strike": 734}]
    line = OptionsLayer._expiration_line(legs, dte=45)
    assert "2026-07-17" in line
    assert "45" in line


def test_expiration_line_falls_back_to_dte_when_no_date():
    line = OptionsLayer._expiration_line([{"strike": 739}], dte=45)
    assert "45" in line
