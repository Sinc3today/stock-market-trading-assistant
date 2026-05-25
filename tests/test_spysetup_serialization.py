"""
tests/test_spysetup_serialization.py

Regression test for: "Object of type SPYSetup is not JSON serializable"

Root cause: TradeLogger.log_alert() appends the raw alert dict (which
contains '_spy_setup': SPYSetup) to a list and then calls json.dump
without a custom encoder. This explodes with a TypeError every time
the swing scanner or intraday scanner fires a SPY alert.

The fix: strip underscore-prefixed keys (private/non-serializable
pass-through fields) before json.dump in TradeLogger._save().
"""

import json
import os
import tempfile

import pytest

from dataclasses import asdict
from signals.spy_options_engine import SPYSetup
from journal.trade_logger import TradeLogger


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _make_spy_alert() -> dict:
    """Build a realistic alert dict as produced by the swing + intraday scanners."""
    setup = SPYSetup(
        strategy="iron_condor",
        conviction="high",
        timeframe="swing",
        score=72,
        reasons=["ADX calm", "VIX low", "range-bound"],
        direction="NEUTRAL",
        spy_price=530.25,
    )
    return {
        "ticker":      "SPY",
        "timestamp":   "2026-05-26 09:35 AM EST",
        "mode":        "Swing",
        "timeframe":   "day",
        "direction":   "NEUTRAL",
        "tier":        "high_conviction",
        "emoji":       "🔴",
        "final_score": 72,
        "strategy":    "iron_condor",
        "setup_tags":  ["ADX calm", "VIX low", "range-bound"],
        "_spy_setup":  setup,   # ← the non-serializable field
    }


# ─────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────

class TestSPYSetupSerialization:
    """TradeLogger must not blow up when an alert contains a SPYSetup."""

    def test_log_alert_with_spysetup_does_not_raise(self, tmp_path):
        """
        Core regression: log_alert() must succeed when the alert dict
        contains '_spy_setup': SPYSetup.  Before the fix this raised:
            TypeError: Object of type SPYSetup is not JSON serializable
        """
        tl = TradeLogger()
        tl.alert_log_path = str(tmp_path / "alerts.json")

        alert = _make_spy_alert()

        # Must not raise — this is the bug gate
        tl.log_alert(alert)

    def test_log_alert_persists_serializable_fields(self, tmp_path):
        """
        After log_alert() the JSON file must exist and contain the
        standard fields.  The _spy_setup key must NOT surface as a
        raw object reference (it may be absent or stringified, both OK).
        """
        tl = TradeLogger()
        tl.alert_log_path = str(tmp_path / "alerts.json")

        tl.log_alert(_make_spy_alert())

        with open(tl.alert_log_path) as f:
            stored = json.load(f)

        assert len(stored) == 1
        entry = stored[0]

        # Core fields must round-trip intact
        assert entry["ticker"] == "SPY"
        assert entry["final_score"] == 72
        assert entry["strategy"] == "iron_condor"

        # _spy_setup must NOT be a raw SPYSetup object (it would have
        # caused json.load to fail if it were — but be explicit).
        if "_spy_setup" in entry:
            assert not isinstance(entry["_spy_setup"], SPYSetup), (
                "_spy_setup survived as a typed object inside the JSON log"
            )

    def test_multiple_spy_alerts_accumulate(self, tmp_path):
        """
        log_alert() called twice must produce a list of 2 entries,
        confirming the load-append-save cycle works end-to-end.
        """
        tl = TradeLogger()
        tl.alert_log_path = str(tmp_path / "alerts.json")

        tl.log_alert(_make_spy_alert())
        tl.log_alert(_make_spy_alert())

        with open(tl.alert_log_path) as f:
            stored = json.load(f)

        assert len(stored) == 2

    def test_non_spy_alert_still_works(self, tmp_path):
        """
        Non-SPY alerts (no _spy_setup) must still log correctly after
        the fix — confirm the fix doesn't break the happy path.
        """
        tl = TradeLogger()
        tl.alert_log_path = str(tmp_path / "alerts.json")

        plain_alert = {
            "ticker":      "AAPL",
            "timestamp":   "2026-05-26 09:35 AM EST",
            "mode":        "Swing",
            "final_score": 55,
            "direction":   "BULLISH",
        }
        tl.log_alert(plain_alert)

        with open(tl.alert_log_path) as f:
            stored = json.load(f)

        assert stored[0]["ticker"] == "AAPL"
