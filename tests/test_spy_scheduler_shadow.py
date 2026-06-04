"""tests/test_spy_scheduler_shadow.py
Task 6: Wire run_shadow into the daily scheduler job.

Four tests:
  1. _run_daily_shadow invokes run_shadow with the correct spot/ivr kwargs.
  2. _run_daily_shadow swallows exceptions so a shadow failure can never
     disturb the real daily play (Standing Rule #10).
  3. _regime_and_levels_from_brief round-trips a realistic extension-skip brief
     dict and produces a RegimeResult that _is_extension_skip() recognises.
  4. job_spy_premarket calls run_shadow with the spot/ivr from brief["metrics"]
     when the brief is an extension-skip (end-to-end wiring seam).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from types import SimpleNamespace

# ── Realistic extension-skip brief (shape produced by MorningBriefer /
#    asdict(PlayCard)).  regime is stored as the enum VALUE string. ───────────
EXTENSION_SKIP_BRIEF = {
    "regime":     "trending_up_calm",
    "tradeable":  False,
    "play":       "SKIP — trend too extended (wait for pullback)",
    "confidence": 0.6,
    "reasons":    [
        "SPY is 8.3% above the 200-day MA — over-extended, pullback risk",
        "ADX 31 confirms trend strength but extension gate is active",
    ],
    "metrics": {
        "spy_close": 760.0,
        "ivr":       55.0,
        "vix":       14.2,
        "adx":       31.0,
        "ma200":     701.4,
        "ma200_dist_%": 8.3,
    },
    "discord_message": "🛑 **STANDING DOWN TODAY — SPY [45DTE]** (2026-05-30)\n...",
}


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


# ─────────────────────────────────────────────────────────────────────────────
# NEW: brief-dict → RegimeResult reconstruction wiring seam
# ─────────────────────────────────────────────────────────────────────────────

def test_regime_and_levels_from_brief_roundtrip():
    """_regime_and_levels_from_brief produces a RegimeResult that:
    - carries the correct Regime enum (not just the string value)
    - is detected as extension-skip by _is_extension_skip
    - returns spot == metrics["spy_close"] and ivr == metrics["ivr"]
    """
    import scheduler.spy_daily_scheduler as sch
    from signals.regime_detector import Regime
    from learning.shadow_tester import _is_extension_skip

    rr, spot, ivr = sch._regime_and_levels_from_brief(EXTENSION_SKIP_BRIEF)

    # regime enum is correctly reconstructed from the string value
    assert rr.regime is Regime.TRENDING_UP_CALM

    # tradeable, play, confidence round-trip
    assert rr.tradeable is False
    assert "extended" in rr.play.lower()
    assert abs(rr.confidence - 0.6) < 1e-9

    # metrics values are extracted correctly
    assert spot == 760.0
    assert ivr  == 55.0

    # the shadow-tester gate recognises this as an extension-skip
    assert _is_extension_skip(rr) is True


def test_job_spy_premarket_wires_brief_to_run_shadow(monkeypatch):
    """job_spy_premarket calls run_shadow with the spot and ivr taken from
    brief["metrics"] when the brief describes an extension-skip day.

    This is the end-to-end wiring seam: a rename of PlayCard.play or a change
    in the Regime enum values would cause _regime_and_levels_from_brief to
    reconstruct wrongly, and this test would catch it immediately.
    """
    import scheduler.spy_daily_scheduler as sch

    # --- stub out everything job_spy_premarket touches ---

    # config.is_trading_day → True (so the job doesn't short-circuit)
    monkeypatch.setattr("config.is_trading_day", lambda dt: True)

    # MorningBriefer.build_today returns our canned extension-skip brief
    class FakeBriefer:
        def build_today(self):
            return EXTENSION_SKIP_BRIEF

    class FakeStrategy:
        pass

    monkeypatch.setattr(sch, "SPYDailyStrategy",
                        lambda **kw: FakeStrategy())
    monkeypatch.setattr(sch, "MorningBriefer",
                        lambda **kw: FakeBriefer())
    # EarningsCalendar / EarningsHistory — not under test; silence them
    monkeypatch.setattr(sch, "EarningsCalendar", lambda **kw: None)
    monkeypatch.setattr(sch, "EarningsHistory",  lambda **kw: None)

    # Capture run_shadow calls
    shadow_calls = []

    def fake_run_shadow(rr, **kw):
        shadow_calls.append({"rr": rr, **kw})
        return {"recorded": True}

    monkeypatch.setattr(sch, "run_shadow", fake_run_shadow)

    # post_fn — not under test
    posted = []
    sch.job_spy_premarket(
        polygon_client = None,
        vix_client     = None,
        ivr_client     = None,
        post_fn        = posted.append,
        event_calendar = None,
    )

    # run_shadow must have been called exactly once
    assert len(shadow_calls) == 1, "run_shadow was not called exactly once"

    call = shadow_calls[0]

    # spot and ivr must match brief["metrics"]
    assert call["spot"] == EXTENSION_SKIP_BRIEF["metrics"]["spy_close"]
    assert call["ivr"]  == EXTENSION_SKIP_BRIEF["metrics"]["ivr"]

    # the RegimeResult passed to run_shadow must be extension-skip-detectable
    from learning.shadow_tester import _is_extension_skip
    assert _is_extension_skip(call["rr"]) is True
