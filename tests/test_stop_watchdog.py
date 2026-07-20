"""tests/test_stop_watchdog.py -- smart-stop watchdog (the trade-copilot core).

The stop keys off the UNDERLYING (SPY vs the short strikes), NOT the option mark
— which is what RH can't do for a condor and why RH stops trip on spread blips.
It warns as SPY approaches a short strike so you can close on RH before max loss.
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


def test_short_strikes_extracted_from_condor():
    from alerts.stop_watchdog import short_strikes
    sp, sc = short_strikes(_condor_legs())
    assert sp == 700 and sc == 776


def test_no_stop_when_spot_well_inside():
    from alerts.stop_watchdog import stop_signal
    trig, reason = stop_signal(_condor_legs(), spot=740.0, buffer_pct=0.005)
    assert trig is False


def test_stop_when_spot_approaches_short_put():
    from alerts.stop_watchdog import stop_signal
    # short put 700; buffer 0.5% -> warn at <= 703.5
    trig, reason = stop_signal(_condor_legs(), spot=703.0, buffer_pct=0.005)
    assert trig is True
    assert "put" in reason.lower()


def test_stop_when_spot_approaches_short_call():
    from alerts.stop_watchdog import stop_signal
    # short call 776; buffer 0.5% -> warn at >= 772.1
    trig, reason = stop_signal(_condor_legs(), spot=773.0, buffer_pct=0.005)
    assert trig is True
    assert "call" in reason.lower()


def test_stop_uses_underlying_not_option_price():
    # a vertical (one short leg) still works
    from alerts.stop_watchdog import stop_signal
    legs = [{"action": "SELL", "option_type": "PUT", "strike": 700},
            {"action": "BUY",  "option_type": "PUT", "strike": 695}]
    assert stop_signal(legs, spot=701.0, buffer_pct=0.005)[0] is True
    assert stop_signal(legs, spot=720.0, buffer_pct=0.005)[0] is False


def test_check_positions_fires_emergency_once_per_position():
    from alerts.stop_watchdog import check_open_positions

    class _Rec:
        def get_open_trades(self):
            return [{"trade_id": "T1", "ticker": "SPY", "strategy": "iron_condor",
                     "book": "disciplined", "legs": _condor_legs()}]

    class _Push:
        def __init__(self): self.sent = []
        def send(self, title, msg, priority=0, **kw): self.sent.append(priority); return True

    push = _Push(); alerted = set()
    # SPY near the short put -> one emergency alert
    n = check_open_positions(_Rec(), spot=702.0, pushover=push, alerted=alerted,
                             buffer_pct=0.005)
    assert n == 1 and push.sent == [2] and "T1" in alerted
    # second pass same position -> deduped, no new alert
    n2 = check_open_positions(_Rec(), spot=702.0, pushover=push, alerted=alerted,
                              buffer_pct=0.005)
    assert n2 == 0 and push.sent == [2]


def test_debit_spread_is_not_stop_watched():
    # The 2026-07-20 flood: a bear put DEBIT spread (BUY 743P / SELL 740P) had
    # its short put flagged as a stop as SPY approached 740 — but that's the
    # PROFIT side and max loss is the (defined) debit. The watchdog must skip it.
    from alerts.stop_watchdog import check_open_positions

    class _Rec:
        def get_open_trades(self):
            return [{"trade_id": "PDS1", "ticker": "SPY", "strategy": "put_debit_spread",
                     "book": "disciplined",
                     "legs": [{"action": "BUY",  "option_type": "PUT", "strike": 743.0},
                              {"action": "SELL", "option_type": "PUT", "strike": 740.0}]}]

    class _Push:
        def __init__(self): self.sent = []
        def send(self, title, msg, priority=0, **kw): self.sent.append(priority); return True

    push = _Push()
    n = check_open_positions(_Rec(), spot=742.84, pushover=push, alerted=set(),
                             buffer_pct=0.005)
    assert n == 0 and push.sent == []          # NO stop alert on a debit spread


def test_credit_structures_still_watched():
    # Regression guard: the fix must NOT silence real credit-structure stops.
    from alerts.stop_watchdog import check_open_positions

    class _Rec:
        def get_open_trades(self):
            return [{"trade_id": "CS1", "ticker": "SPY", "strategy": "credit_spread",
                     "book": "live",
                     "legs": [{"action": "SELL", "option_type": "PUT", "strike": 740.0},
                              {"action": "BUY",  "option_type": "PUT", "strike": 735.0}]}]

    class _Push:
        def __init__(self): self.sent = []
        def send(self, title, msg, priority=0, **kw): self.sent.append(priority); return True

    push = _Push()
    n = check_open_positions(_Rec(), spot=740.5, pushover=push, alerted=set(),
                             buffer_pct=0.005)
    assert n == 1 and push.sent == [2]         # credit-spread short-put breach still fires
