"""tests/test_spy_scheduler_shadow.py
Task 6: Wire run_shadow into the daily scheduler job.

Two tests:
  1. _run_daily_shadow invokes run_shadow with the correct spot/ivr kwargs.
  2. _run_daily_shadow swallows exceptions so a shadow failure can never
     disturb the real daily play (Standing Rule #10).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from types import SimpleNamespace


def test_run_daily_shadow_invokes_run_shadow_on_extension_skip(monkeypatch):
    import scheduler.spy_daily_scheduler as sch
    calls = []
    monkeypatch.setattr(sch, "run_shadow",
                        lambda rr, **kw: calls.append(kw) or {"recorded": True})
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended",
                         reasons=[], metrics={"spy_close": 760.0, "ivr": 55.0})
    sch._run_daily_shadow(rr, spot=760.0, ivr=55.0)
    assert len(calls) == 1
    assert calls[0]["spot"] == 760.0 and calls[0]["ivr"] == 55.0


def test_run_daily_shadow_swallows_errors(monkeypatch):
    import scheduler.spy_daily_scheduler as sch

    def boom(rr, **kw):
        raise RuntimeError("shadow blew up")

    monkeypatch.setattr(sch, "run_shadow", boom)
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended", reasons=[], metrics={})
    # must NOT raise (the real daily play must never be disturbed)
    sch._run_daily_shadow(rr, spot=760.0, ivr=55.0)
