"""tests/test_todays_call_push.py -- the daily 'today's call' push (T1.5).

Skip days used to be silent — 'bot skipped' looked identical to 'bot broke'.
Every trading morning now pushes exactly one call: the play or the stand-down.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scheduler.spy_daily_scheduler import _todays_call_push


class _Play:
    def __init__(self): self.calls = []
    def __call__(self, title, body): self.calls.append((title, body))


def test_tradeable_day_pushes_the_play():
    p = _Play()
    _todays_call_push({"tradeable": True, "regime": "choppy_low_vol",
                       "strategy": "iron_condor", "play": "IRON CONDOR"}, p)
    assert len(p.calls) == 1
    assert "iron condor" in p.calls[0][0].lower()


def test_skip_day_pushes_stand_down_with_reason():
    p = _Play()
    _todays_call_push({"tradeable": False, "regime": "trending_high_vol",
                       "skip_conditions": ["VIX inverted — premium selling unsafe"]}, p)
    assert len(p.calls) == 1
    title, body = p.calls[0]
    assert "standing down" in title.lower()
    assert "VIX inverted" in body


def test_no_play_fn_is_safe():
    _todays_call_push({"tradeable": True}, None)   # must not raise
