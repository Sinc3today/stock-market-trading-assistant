import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scanners.intraday_scanner import _maybe_play_on_open, set_play_fn


def test_disciplined_open_pushes_play():
    plays = []
    set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"strategy": "iron_condor", "dte_bucket": "1-3DTE", "book": "disciplined"},
                        {"trade_id": "T9", "recorded": True})
    assert len(plays) == 1 and "T9" in plays[0]["body"]


def test_learning_open_does_not_push():
    plays = []
    set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"strategy": "iron_condor", "dte_bucket": "0DTE", "book": "learning"},
                        {"trade_id": "T10", "recorded": True})
    assert plays == []


def test_unrecorded_does_not_push():
    plays = []
    set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"book": "disciplined"}, {"trade_id": None, "recorded": False})
    assert plays == []


def test_instance_set_play_fn_routes_through_maybe_play(monkeypatch):
    """Regression: main.py wires the play hook on the IntradayScanner INSTANCE
    (intraday_scanner.set_play_fn). The instance method must exist and feed the
    same module hook _maybe_play_on_open reads. (A module-only set_play_fn caused
    an AttributeError crash-loop at startup on 2026-06-02.)"""
    from scanners.intraday_scanner import IntradayScanner, _maybe_play_on_open
    plays = []
    IntradayScanner().set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"strategy": "iron_condor", "dte_bucket": "1-3DTE", "book": "disciplined"},
                        {"trade_id": "TZ", "recorded": True})
    assert len(plays) == 1 and "TZ" in plays[0]["body"]
