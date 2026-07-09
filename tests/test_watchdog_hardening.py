"""tests/test_watchdog_hardening.py -- stop-watchdog data-failure escalation.

Audit T1.4: when Polygon failed, the watchdog silently returned — stop coverage
could be down for hours with no signal. Now: yfinance fallback for spot, and an
explicit alert after 3 consecutive data failures (once per day).
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_tracker_alerts_after_three_consecutive_failures():
    from alerts.stop_watchdog import DataFailureTracker
    t = DataFailureTracker(threshold=3)
    assert t.record_failure(today=date(2026, 7, 9)) is False
    assert t.record_failure(today=date(2026, 7, 9)) is False
    assert t.record_failure(today=date(2026, 7, 9)) is True     # 3rd -> alert
    # further failures the same day don't re-alert
    assert t.record_failure(today=date(2026, 7, 9)) is False


def test_tracker_resets_on_success():
    from alerts.stop_watchdog import DataFailureTracker
    t = DataFailureTracker(threshold=3)
    t.record_failure(today=date(2026, 7, 9))
    t.record_failure(today=date(2026, 7, 9))
    t.record_success()
    assert t.record_failure(today=date(2026, 7, 9)) is False    # counter reset


def test_tracker_realerts_next_day():
    from alerts.stop_watchdog import DataFailureTracker
    t = DataFailureTracker(threshold=3)
    for _ in range(3):
        t.record_failure(today=date(2026, 7, 9))
    for i in range(2):
        assert t.record_failure(today=date(2026, 7, 10)) is False
    assert t.record_failure(today=date(2026, 7, 10)) is True    # new day re-alerts


def test_resolve_spot_falls_back():
    from alerts.stop_watchdog import resolve_spot
    assert resolve_spot(lambda: 750.5, lambda: 751.0) == 750.5    # primary wins
    assert resolve_spot(lambda: None, lambda: 751.0) == 751.0     # fallback
    def boom(): raise RuntimeError("down")
    assert resolve_spot(boom, lambda: 751.0) == 751.0             # primary raises
    assert resolve_spot(boom, boom) is None                       # both dead
