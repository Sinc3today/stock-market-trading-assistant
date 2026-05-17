"""
tests/test_backtest_rerun.py -- pure helpers for the rerun CLI.

Skips the actual SPYBacktest execution (slow + needs CSV); covers:
  - compute_summary() shape and aggregation math
  - format_deltas() text rendering for positive/negative/missing deltas
  - refresh_history.diff_against_existing() CSV diff
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.rerun            import compute_summary, format_deltas
from backtests.refresh_history  import diff_against_existing


# ── compute_summary ───────────────────────────────────

def _fake_results() -> pd.DataFrame:
    """Hand-built results table covering both regimes + outcomes."""
    rows = [
        # choppy_low_vol: 2 wins, 1 loss (66.7%), all tradeable
        {"date": "2025-01-02", "regime": "choppy_low_vol",
         "play": "iron_condor", "tradeable": True,
         "vix": 14.0, "ivr": 40, "adx": 18, "ma200_dist": 2.0,
         "outcome": "win",  "pnl": 130, "confidence": 0.7},
        {"date": "2025-01-03", "regime": "choppy_low_vol",
         "play": "iron_condor", "tradeable": True,
         "vix": 14.5, "ivr": 41, "adx": 19, "ma200_dist": 2.1,
         "outcome": "loss", "pnl": -220, "confidence": 0.6},
        {"date": "2025-01-04", "regime": "choppy_low_vol",
         "play": "iron_condor", "tradeable": True,
         "vix": 14.2, "ivr": 40, "adx": 18, "ma200_dist": 2.0,
         "outcome": "win",  "pnl": 130, "confidence": 0.7},
        # trending_up_calm: 1 win, 1 loss (50%)
        {"date": "2025-01-05", "regime": "trending_up_calm",
         "play": "bull_debit", "tradeable": True,
         "vix": 13.0, "ivr": 30, "adx": 28, "ma200_dist": 5.0,
         "outcome": "win",  "pnl": 150, "confidence": 0.8},
        {"date": "2025-01-06", "regime": "trending_up_calm",
         "play": "bull_debit", "tradeable": True,
         "vix": 13.2, "ivr": 31, "adx": 28, "ma200_dist": 5.1,
         "outcome": "loss", "pnl": -200, "confidence": 0.7},
        # trending_high_vol: not tradeable (counts as skip)
        {"date": "2025-01-07", "regime": "trending_high_vol",
         "play": "skip", "tradeable": False,
         "vix": 25.0, "ivr": 70, "adx": 30, "ma200_dist": 0.0,
         "outcome": "skip", "pnl": 0, "confidence": 0.5},
    ]
    return pd.DataFrame(rows)


def test_compute_summary_top_level_shape():
    s = compute_summary(_fake_results(), years=1, source="local")
    assert s["years"] == 1
    assert s["source"].startswith("rerun_cli")
    assert "version" in s
    assert set(s["overview"]).issuperset(
        {"sharpe", "win_rate_pct", "trade_days", "skip_days", "total_pnl"}
    )
    assert isinstance(s["by_regime"], list) and len(s["by_regime"]) >= 1


def test_compute_summary_win_rate_aggregation():
    s = compute_summary(_fake_results(), years=1, source="local")
    # 5 closed trades (3 IC + 2 bull_debit), 3 wins → 60.0%
    assert s["overview"]["win_rate_pct"] == pytest.approx(60.0)
    assert s["overview"]["trade_days"]   == 5
    assert s["overview"]["skip_days"]    == 1
    assert s["overview"]["total_pnl"]    == 130 - 220 + 130 + 150 - 200
    # Sharpe is computed (not zero); sign depends on the fake P&L mix.
    assert isinstance(s["overview"]["sharpe"], float)
    assert s["overview"]["sharpe"] != 0.0


def test_compute_summary_per_regime_win_rates():
    s = compute_summary(_fake_results(), years=1, source="local")
    by = {r["regime"]: r for r in s["by_regime"]}
    assert by["choppy_low_vol"]["win_rate_pct"]   == pytest.approx(66.7)
    assert by["trending_up_calm"]["win_rate_pct"] == pytest.approx(50.0)
    # Non-tradeable regime stays in the breakdown but with tradeable=False
    assert by["trending_high_vol"]["tradeable"] is False
    assert "Iron condor" in by["choppy_low_vol"]["note"]


def test_compute_summary_handles_empty_results():
    s = compute_summary(pd.DataFrame(), years=5, source="local")
    assert s["overview"]["sharpe"] == 0.0
    assert s["overview"]["win_rate_pct"] == 0.0
    assert s["by_regime"] == []


# ── format_deltas ─────────────────────────────────────

def test_format_deltas_positive_delta_uses_plus_sign():
    old = {"source": "static_defaults",
           "overview": {"sharpe": 1.50, "win_rate_pct": 48.0, "trade_days": 800}}
    new = {"overview": {"sharpe": 1.73, "win_rate_pct": 50.3, "trade_days": 850}}
    txt = format_deltas(old, new)
    assert "1.5 → 1.73" in txt or "1.50 → 1.73" in txt
    assert "+0.23" in txt
    assert "+2.30" in txt   # win_rate_pct +2.3
    assert "static_defaults" in txt


def test_format_deltas_negative_delta_uses_minus_sign():
    old = {"overview": {"sharpe": 1.73, "win_rate_pct": 50.3, "trade_days": 850}}
    new = {"overview": {"sharpe": 1.50, "win_rate_pct": 48.0, "trade_days": 800}}
    txt = format_deltas(old, new)
    # We use a unicode minus sign in the formatter, not ASCII '-'
    assert "−0.23" in txt
    assert "−2.30" in txt


def test_format_deltas_handles_missing_fields():
    old = {"overview": {"sharpe": None, "win_rate_pct": 50.0}}
    new = {"overview": {"sharpe": 1.73}}
    txt = format_deltas(old, new)
    # Doesn't crash; missing values render as '—' or '?' rather than a number
    assert "—" in txt or "?" in txt


# ── refresh_history.diff_against_existing ─────────────

def test_diff_against_existing_no_csv_means_all_new(tmp_path):
    new_df = pd.DataFrame({"close": [100, 101, 102]},
                          index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-04"]).date)
    delta = diff_against_existing(new_df, csv_path=str(tmp_path / "nonexistent.csv"))
    assert delta["old_n"] == 0
    assert delta["new_n"] == 3
    assert delta["added"] == 3
    assert delta["removed"] == 0


def test_diff_against_existing_counts_added_and_removed(tmp_path):
    csv_path = str(tmp_path / "spy.csv")
    pd.DataFrame(
        {"close": [100, 101, 102]},
        index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-04"]).date,
    ).to_csv(csv_path)
    new_df = pd.DataFrame(
        {"close": [101, 102, 103, 104]},     # dropped 2025-01-02, added 03/04/etc
        index=pd.to_datetime(["2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06"]).date,
    )
    delta = diff_against_existing(new_df, csv_path=csv_path)
    assert delta["old_n"] == 3
    assert delta["new_n"] == 4
    assert delta["added"]   == 2   # 2025-01-05, 2025-01-06
    assert delta["removed"] == 1   # 2025-01-02


def test_diff_against_existing_treats_corrupt_csv_as_empty(tmp_path):
    csv_path = str(tmp_path / "spy.csv")
    with open(csv_path, "w") as f:
        f.write("this is not a csv\n,,,\n")
    new_df = pd.DataFrame({"close": [100]},
                          index=pd.to_datetime(["2025-01-02"]).date)
    delta = diff_against_existing(new_df, csv_path=csv_path)
    # Should still produce a delta (treating bad CSV as empty rather than crashing)
    assert delta["new_n"] == 1
    assert delta["added"] >= 0
