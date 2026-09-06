"""tests/test_dte_ladder_exit_study.py -- the ladder exit study's machinery.

The study drives a decision about the core strategy, so the parts that could
silently lie are pinned: the calendar/trading-day resolution that produced two
wrong publications, and the tail statistics that are the whole reason this
study exists (mean P&L could not settle the 45DTE question).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests import dte_ladder_exit_study as st


def _rows(pnls, era="new", mae=-10.0):
    return [{"pnl": p, "mae": mae, "era": era} for p in pnls]


# ── tail statistics ──────────────────────────────────────────────

def test_stats_needs_a_minimum_sample():
    assert st.stats(_rows([1.0] * 29)) is None
    assert st.stats(_rows([1.0] * 30)) is not None


def test_stats_reports_median_not_just_mean():
    """A mean carried by one huge winner is not an edge."""
    s = st.stats(_rows([-1.0] * 39 + [1000.0]))
    assert s["avg"] > 0 and s["median"] < 0


def test_p05_captures_the_tail_the_average_hides():
    s = st.stats(_rows([10.0] * 95 + [-500.0] * 5))
    assert s["p05"] <= -500.0
    assert s["worst"] == -500.0


def test_win_loss_ratio_is_reported():
    s = st.stats(_rows([100.0] * 50 + [-200.0] * 50))
    assert s["ratio"] == pytest.approx(0.5)


def test_sharpe_is_mean_over_sigma():
    s = st.stats(_rows([10.0] * 50 + [-10.0] * 50))
    assert s["sharpe"] == pytest.approx(0.0, abs=1e-9)


def test_mae_is_averaged_across_trades():
    rows = _rows([10.0] * 30, mae=-50.0)
    assert st.stats(rows)["mae"] == pytest.approx(-50.0)


def test_oos_requires_both_eras_positive():
    both = _rows([10.0] * 20, era="old") + _rows([10.0] * 20, era="new")
    assert st.stats(both)["pass"]
    split = _rows([-10.0] * 20, era="old") + _rows([30.0] * 20, era="new")
    assert not st.stats(split)["pass"], "positive overall but old era negative"


def test_single_era_never_passes():
    assert not st.stats(_rows([10.0] * 40, era="new"))["pass"]


# ── the calendar/trading-day contract ────────────────────────────

def test_simulate_resolves_exit_by_calendar_date(monkeypatch):
    """DTE is calendar days; the index is trading days. Walking rows overshoots
    ~40% and can run past expiry — the bug that produced two wrong writeups."""
    import pandas as pd
    idx = pd.bdate_range("2026-01-05", periods=60)
    df = pd.DataFrame({"close": [700.0] * 60, "vix": [16.0] * 60}, index=idx)
    out = st.simulate(df, 0, dte=45, close_dte=21)
    assert out is not None
    pnl, mae = out
    assert isinstance(pnl, float) and mae <= 0


def test_simulate_returns_none_when_the_window_runs_off_the_data():
    import pandas as pd
    idx = pd.bdate_range("2026-01-05", periods=3)
    df = pd.DataFrame({"close": [700.0] * 3, "vix": [16.0] * 3}, index=idx)
    assert st.simulate(df, 0, dte=45, close_dte=21) is None


def test_mae_is_never_positive():
    """MAE is the WORST mark seen; a profitable path must still report <= 0."""
    import pandas as pd
    idx = pd.bdate_range("2026-01-05", periods=40)
    df = pd.DataFrame({"close": [700.0] * 40, "vix": [16.0] * 40}, index=idx)
    out = st.simulate(df, 0, dte=30, close_dte=3)
    assert out is None or out[1] <= 0


def test_ladder_covers_every_rung_we_run_or_might_run():
    assert set(st.LADDER) == {45, 30, 21, 14, 7}
    for dte, candidates in st.LADDER.items():
        assert candidates == sorted(candidates)
        assert all(c < dte for c in candidates), "close_dte must be inside the trade"


def test_configured_rules_match_the_live_modules():
    """If a live CLOSE_DTE moves, this study's baseline must move with it."""
    from learning import seven_dte_forward as sdf
    assert st.CONFIGURED[7] == sdf.CLOSE_DTE
