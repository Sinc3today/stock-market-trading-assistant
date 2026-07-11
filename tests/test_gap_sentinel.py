"""tests/test_gap_sentinel.py -- Sunday-night weekend-gap early warning."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts.gap_sentinel import check_sunday_gap


class _Rec:
    def __init__(self, trades): self._t = trades
    def get_open_trades(self): return self._t


class _Send:
    def __init__(self): self.sent = []
    def __call__(self, title, message, priority=0, **k): self.sent.append(title)


def _condor(book="live"):
    return {"book": book, "ticker": "SPY", "strategy": "iron_condor",
            "legs": [{"action": "SELL", "option_type": "PUT", "strike": 740},
                     {"action": "SELL", "option_type": "CALL", "strike": 790}]}


def test_alerts_when_gap_forming_and_positions_open():
    s = _Send()
    fired = check_sunday_gap(_Rec([_condor()]), s, move_fn=lambda: (-0.9, 6100.0, 6155.0))
    assert fired and "gap" in s.sent[0].lower()


def test_silent_when_calm_or_flat():
    s = _Send()
    assert not check_sunday_gap(_Rec([_condor()]), s, move_fn=lambda: (0.2, 6160.0, 6150.0))
    assert s.sent == []


def test_silent_with_no_positions():
    s = _Send()
    assert not check_sunday_gap(_Rec([]), s, move_fn=lambda: (-2.0, 0, 0))
    assert s.sent == []


def test_survives_dead_feed():
    s = _Send()
    assert not check_sunday_gap(_Rec([_condor()]), s, move_fn=lambda: None)
