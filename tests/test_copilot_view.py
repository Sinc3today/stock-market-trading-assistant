"""tests/test_copilot_view.py -- companion-screen pure helpers.

RH-shaped leg lines + 3-tier stop status (SAFE/WATCH/NEAR STOP) + the watchdog
monitoring 'live' (user-logged real) positions, not just bot 'disciplined' ones.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def _condor_legs():
    return [
        {"action": "BUY",  "option_type": "PUT",  "strike": 695},
        {"action": "SELL", "option_type": "PUT",  "strike": 700},
        {"action": "SELL", "option_type": "CALL", "strike": 776},
        {"action": "BUY",  "option_type": "CALL", "strike": 781},
    ]


def test_rh_leg_lines_match_broker_wording():
    from alerts.stop_watchdog import rh_leg_lines
    lines = rh_leg_lines(_condor_legs())
    assert "SELL $700 PUT" in lines
    assert "BUY $781 CALL" in lines
    assert len(lines) == 4


def test_rh_leg_lines_order_buy_first_calls_then_puts():
    # user preference: buy call, sell call, buy put, sell put
    from alerts.stop_watchdog import rh_leg_lines
    lines = rh_leg_lines(_condor_legs())   # stored order is mixed
    assert lines == ["BUY $781 CALL", "SELL $776 CALL",
                     "BUY $695 PUT",  "SELL $700 PUT"]


def test_position_status_three_tiers():
    from alerts.stop_watchdog import position_status
    # well inside -> SAFE
    assert position_status(_condor_legs(), 740.0, buffer_pct=0.005)[0] == "SAFE"
    # within the stop buffer of short put (<=703.5) -> NEAR STOP
    assert position_status(_condor_legs(), 702.0, buffer_pct=0.005)[0] == "NEAR STOP"
    # within the WATCH band (2x buffer, ~707) but not the stop -> WATCH
    assert position_status(_condor_legs(), 706.0, buffer_pct=0.005)[0] == "WATCH"


def test_watchdog_monitors_live_positions_by_default():
    from alerts.stop_watchdog import check_open_positions

    class _Rec:
        def get_open_trades(self):
            return [{"trade_id": "L1", "ticker": "SPY", "strategy": "iron_condor",
                     "book": "live", "legs": _condor_legs()}]

    class _Push:
        def __init__(self): self.sent = []
        def send(self, t, m, priority=0, **k): self.sent.append(priority); return True

    push = _Push()
    # SPY near short put; 'live' (user-logged) positions must be watched too
    n = check_open_positions(_Rec(), spot=702.0, pushover=push, alerted=set(),
                             buffer_pct=0.005)
    assert n == 1 and push.sent == [2]
