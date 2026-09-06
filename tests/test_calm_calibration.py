"""tests/test_calm_calibration.py -- is "calm" a true label?

The August 2026 damage did not come from an untested regime. It came from a
regime we had tested and labelled CALM: VIX stayed under 18 while SPY ran
+5.7% in four days and drove through the condors' short calls. Implied vol was
low; realised vol was not.

This instrument scores the claim the classifier makes every single day —
"VIX < 18 and ADX < 32 means the next 5 sessions stay inside the band we are
selling" — by comparing the move VIX IMPLIED against the move that actually
happened. Event rate is ~100% of days, so it answers in weeks, not years.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import calm_calibration as cc


# ── the implied band ─────────────────────────────────────────────

def test_implied_move_scales_with_vol():
    lo = cc.implied_move_pct(vix=12.0, days=5)
    hi = cc.implied_move_pct(vix=24.0, days=5)
    assert hi == pytest.approx(lo * 2, rel=1e-6)


def test_implied_move_scales_with_root_time():
    one = cc.implied_move_pct(vix=16.0, days=5)
    four = cc.implied_move_pct(vix=16.0, days=20)
    assert four == pytest.approx(one * 2, rel=1e-6)


def test_implied_move_is_zero_without_vol():
    assert cc.implied_move_pct(vix=0.0, days=5) == 0.0
    assert cc.implied_move_pct(vix=None, days=5) == 0.0


def test_implied_move_at_vix_16_over_a_week_is_plausible():
    """VIX 16 over 5 sessions ~ 2.2%. Sanity-check the arithmetic."""
    assert 1.8 < cc.implied_move_pct(vix=16.0, days=5) < 2.6


# ── scoring one day ──────────────────────────────────────────────

def test_quiet_day_scores_as_contained():
    r = cc.score_day(vix=16.0, actual_move_pct=0.8, days=5)
    assert r["ratio"] < 1.0
    assert not r["breached"]


def test_violent_day_scores_as_breached():
    """The August case: VIX said calm, SPY moved 5.7%."""
    r = cc.score_day(vix=14.0, actual_move_pct=5.7, days=5)
    assert r["ratio"] > 2.0
    assert r["breached"]


def test_breach_threshold_is_the_short_strike_not_one_sigma():
    """We sell ~0.20-delta shorts, roughly 0.85 sigma — a move past THAT is
    what hurts, not a move past 1 sigma."""
    implied = cc.implied_move_pct(vix=16.0, days=5)
    just_under = cc.score_day(vix=16.0, actual_move_pct=implied * 0.7, days=5)
    just_over = cc.score_day(vix=16.0, actual_move_pct=implied * 0.95, days=5)
    assert not just_under["breached"]
    assert just_over["breached"]


def test_score_day_handles_missing_inputs():
    assert cc.score_day(vix=None, actual_move_pct=1.0, days=5) is None
    assert cc.score_day(vix=16.0, actual_move_pct=None, days=5) is None


# ── aggregation ──────────────────────────────────────────────────

def test_calibration_reports_breach_rate():
    rows = [cc.score_day(vix=16.0, actual_move_pct=m, days=5)
            for m in (0.5, 0.6, 0.7, 8.0)]
    agg = cc.summarise(rows)
    assert agg["n"] == 4
    assert agg["breaches"] == 1
    assert agg["breach_pct"] == pytest.approx(25.0)


def test_calibration_reports_median_ratio():
    rows = [cc.score_day(vix=16.0, actual_move_pct=m, days=5)
            for m in (1.0, 2.0, 3.0)]
    agg = cc.summarise(rows)
    assert agg["median_ratio"] > 0


def test_wellcalibrated_label_when_realised_matches_implied():
    """If realised tracks implied, the label is honest."""
    implied = cc.implied_move_pct(vix=16.0, days=5)
    rows = [cc.score_day(vix=16.0, actual_move_pct=implied * f, days=5)
            for f in (0.5, 0.6, 0.7, 0.8, 0.5, 0.6)]
    assert cc.summarise(rows)["verdict"] == cc.CALIBRATED


def test_underpriced_label_when_realised_exceeds_implied():
    """The failure mode we are hunting: 'calm' that isn't."""
    rows = [cc.score_day(vix=14.0, actual_move_pct=6.0, days=5) for _ in range(10)]
    assert cc.summarise(rows)["verdict"] == cc.UNDERPRICED


def test_summarise_of_nothing_is_safe():
    agg = cc.summarise([])
    assert agg["n"] == 0 and agg["verdict"] == cc.NO_DATA


def test_summarise_ignores_none_rows():
    rows = [None, cc.score_day(vix=16.0, actual_move_pct=1.0, days=5), None]
    assert cc.summarise(rows)["n"] == 1


# ── persistence ──────────────────────────────────────────────────

def test_log_and_resolve_round_trip(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    cc.log_calm_day("2026-09-01", regime="choppy_low_vol", vix=15.0,
                    adx=18.0, spot=770.0)
    rows = cc.load_all()
    assert len(rows) == 1 and rows[0]["resolved"] is False

    cc.resolve_day("2026-09-01", actual_close=790.0)
    rows = cc.load_all()
    assert rows[0]["resolved"] is True
    assert rows[0]["actual_move_pct"] == pytest.approx(2.597, abs=0.01)
    assert rows[0]["breached"] is True          # 2.6% vs VIX-15 implied ~2.1%


def test_logging_the_same_day_twice_does_not_duplicate(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    cc.log_calm_day("2026-09-01", regime="choppy_low_vol", vix=15.0,
                    adx=18.0, spot=770.0)
    cc.log_calm_day("2026-09-01", regime="choppy_low_vol", vix=15.0,
                    adx=18.0, spot=770.0)
    assert len(cc.load_all()) == 1


def test_unresolved_days_are_findable(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    cc.log_calm_day("2026-09-01", regime="choppy_low_vol", vix=15.0,
                    adx=18.0, spot=770.0)
    cc.log_calm_day("2026-09-02", regime="choppy_low_vol", vix=15.0,
                    adx=18.0, spot=771.0)
    cc.resolve_day("2026-09-01", actual_close=775.0)
    assert [r["date"] for r in cc.unresolved()] == ["2026-09-02"]


def test_resolving_an_unknown_day_is_harmless(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    assert cc.resolve_day("2099-01-01", actual_close=1.0) is False


def test_calibration_report_never_raises_on_empty(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rep = cc.calibration()
    assert rep["n"] == 0 and rep["verdict"] == cc.NO_DATA


# ── scheduler wiring ─────────────────────────────────────────────

def test_calm_calibration_job_is_registered():
    import inspect
    from learning import scheduler as sch
    src = inspect.getsource(sch.register_learning_jobs)
    assert "learning_calm_calibration" in src


def test_job_skips_non_trading_days(monkeypatch):
    from learning import scheduler as sch
    import config
    monkeypatch.setattr(config, "is_trading_day", lambda *a, **k: False)
    called = {"n": 0}

    class _PC:
        def get_bars(self, *a, **k):
            called["n"] += 1
            return None

    sch.job_calm_calibration(_PC())          # must not raise, must not fetch
    assert called["n"] == 0


def test_job_survives_a_data_failure(monkeypatch):
    """Standing rule 10: one job failing never crashes the bot."""
    from learning import scheduler as sch
    import config
    monkeypatch.setattr(config, "is_trading_day", lambda *a, **k: True)

    class _PC:
        def get_bars(self, *a, **k):
            raise RuntimeError("polygon down")

    sch.job_calm_calibration(_PC())          # no exception escapes
