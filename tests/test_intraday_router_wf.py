"""Tests for backtests/intraday_router_wf.py."""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.intraday_router_wf import _MockBroker


def test_mockbroker_empty_returns_zero_opens():
    broker = _MockBroker()
    assert broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="0DTE") == []
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 0


def test_mockbroker_record_open_visible_to_dedup_queries():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    opens = broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="0DTE")
    assert len(opens) == 1
    assert opens[0]["outcome"] == "open"
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 1


def test_mockbroker_different_combos_isolated():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    assert broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="1-3DTE") == []
    assert broker.trades.get_trades_by(strategy="call_debit_spread", dte_bucket="0DTE") == []


import config
from backtests.intraday_router_wf import _bypass_tier_gate


def test_bypass_tier_gate_lowers_minimum_inside_block():
    original = config.ENTRY_TIER_MINIMUM
    with _bypass_tier_gate():
        assert config.ENTRY_TIER_MINIMUM == "watch"
    assert config.ENTRY_TIER_MINIMUM == original


def test_bypass_tier_gate_restores_on_exception():
    original = config.ENTRY_TIER_MINIMUM
    with pytest.raises(RuntimeError, match="boom"):
        with _bypass_tier_gate():
            raise RuntimeError("boom")
    assert config.ENTRY_TIER_MINIMUM == original


def test_bypass_tier_gate_restores_even_after_nested_change():
    """If user code mutates ENTRY_TIER_MINIMUM inside the block, the original
    value (captured at __enter__) is still restored."""
    original = config.ENTRY_TIER_MINIMUM
    with _bypass_tier_gate():
        config.ENTRY_TIER_MINIMUM = "something_else"  # nasty caller
    assert config.ENTRY_TIER_MINIMUM == original


from backtests.intraday_router_wf import generate_windows


def test_generate_windows_full_2024_2025_monthly_step():
    """6mo train / 3mo test / 1mo step over 2024-01-02 to 2025-12-31."""
    wins = list(generate_windows(date(2024, 1, 2), date(2025, 12, 31),
                                 train_months=6, test_months=3, step_months=1))
    # First test window: months 7-9 of 2024 (after the 6mo train).
    # Last possible test: months 10-12 of 2025 (ends on/before 2025-12-31).
    assert len(wins) == 16, f"expected 16 windows, got {len(wins)}"
    # Train always precedes test, no overlap inside a single window.
    for train_range, test_range in wins:
        assert train_range[1] < test_range[0], \
            f"train must end before test starts: {train_range} vs {test_range}"


def test_generate_windows_monotonic_test_starts():
    """Sliding window: each window's test_start is monotonically increasing."""
    wins = list(generate_windows(date(2024, 1, 2), date(2025, 12, 31)))
    test_starts = [test_range[0] for _, test_range in wins]
    assert test_starts == sorted(test_starts)


def test_generate_windows_stops_when_test_would_overshoot_end():
    """No window whose test_range extends past `end`."""
    end = date(2024, 12, 31)
    wins = list(generate_windows(date(2024, 1, 2), end,
                                 train_months=6, test_months=3, step_months=1))
    for _, (_, test_end) in wins:
        assert test_end <= end


from backtests.intraday_router_wf import (
    _strategy_to_structure,
    STRATEGY_NOT_SUPPORTED,
)


def test_strategy_to_structure_iron_condor():
    assert _strategy_to_structure("iron_condor", "neutral") == "iron_condor"


def test_strategy_to_structure_call_debit_spread_bullish():
    assert _strategy_to_structure("call_debit_spread", "bullish") == "bull_debit"


def test_strategy_to_structure_put_debit_spread_bearish():
    assert _strategy_to_structure("put_debit_spread", "bearish") == "bear_debit"


def test_strategy_to_structure_unknown_returns_sentinel():
    assert _strategy_to_structure("rotational_diagonal", "bullish") is STRATEGY_NOT_SUPPORTED


# simulate_short_dte_day is tested via the integration test in Task 12 —
# unit-testing it would re-test simulate_0dte_day, which already has tests
# in backtests/intraday_backtest.py's own suite.


import math
import statistics
from backtests.intraday_router_wf import window_stats


def _trade(pnl, strategy="iron_condor", bucket="0DTE"):
    return {"pnl_dollars": pnl, "strategy": strategy, "dte_bucket": bucket}


def test_window_stats_empty_treatment_returns_zero_n():
    s = window_stats([], [_trade(50), _trade(-30)])
    assert s["n_trades_T"] == 0
    assert s["n_trades_B"] == 2
    assert s["pnl_T"] == 0.0
    assert math.isnan(s["delta_pnl_per_trade"]) or s["delta_pnl_per_trade"] == 0.0


def test_window_stats_computes_deltas():
    t = [_trade(100), _trade(-50), _trade(80)]
    b = [_trade(40),  _trade(-80), _trade(20)]
    s = window_stats(t, b)
    assert s["n_trades_T"] == 3
    assert s["n_trades_B"] == 3
    assert s["pnl_T"] == 130.0
    assert s["pnl_B"] == -20.0
    assert s["delta_pnl_per_trade"] == pytest.approx((130.0/3) - (-20.0/3))
    assert s["win_rate_T"] == pytest.approx(2/3)
    assert s["win_rate_B"] == pytest.approx(2/3)


def test_window_stats_includes_per_bucket_breakdown():
    t = [_trade(100, bucket="0DTE"), _trade(-50, bucket="1-3DTE")]
    b = [_trade(40,  bucket="0DTE"), _trade(20,  bucket="1-3DTE")]
    s = window_stats(t, b)
    assert "by_bucket" in s
    assert s["by_bucket"]["0DTE"]["n_trades_T"] == 1
    assert s["by_bucket"]["1-3DTE"]["n_trades_T"] == 1


def test_window_stats_includes_per_strategy_bucket_breakdown():
    # Mix strategies and buckets across both sides.
    t = [
        _trade(100, strategy="put_debit_spread", bucket="1-3DTE"),
        _trade(-40, strategy="put_debit_spread", bucket="1-3DTE"),
        _trade(60,  strategy="put_debit_spread", bucket="0DTE"),
        _trade(20,  strategy="call_debit_spread", bucket="1-3DTE"),
    ]
    b = [
        _trade(10,  strategy="put_debit_spread", bucket="1-3DTE"),
        _trade(30,  strategy="call_debit_spread", bucket="1-3DTE"),
        _trade(-50, strategy="call_debit_spread", bucket="1-3DTE"),
    ]
    s = window_stats(t, b)
    assert "by_strategy_bucket" in s
    sb = s["by_strategy_bucket"]

    # String keys (JSON-serializable), not tuples.
    assert all(isinstance(k, str) and "|" in k for k in sb)

    pds13 = sb["put_debit_spread|1-3DTE"]
    # Treatment side: two trades 100, -40.
    assert pds13["n_trades_T"] == 2
    assert pds13["pnl_T"] == pytest.approx(60.0)
    assert pds13["mean_T"] == pytest.approx(30.0)
    assert pds13["wins_T"] == 1
    assert pds13["win_rate_T"] == pytest.approx(0.5)
    assert pds13["sharpe_T"] == pytest.approx(
        statistics.mean([100, -40]) / statistics.stdev([100, -40])
    )
    # Baseline side: one trade 10.
    assert pds13["n_trades_B"] == 1
    assert pds13["pnl_B"] == pytest.approx(10.0)
    assert pds13["mean_B"] == pytest.approx(10.0)
    assert pds13["wins_B"] == 1
    assert pds13["win_rate_B"] == pytest.approx(1.0)

    # put_debit_spread|0DTE present only on treatment side.
    pds0 = sb["put_debit_spread|0DTE"]
    assert pds0["n_trades_T"] == 1
    assert pds0["pnl_T"] == pytest.approx(60.0)
    assert pds0["n_trades_B"] == 0
    assert pds0["pnl_B"] == 0.0
    assert pds0["win_rate_B"] == 0.0

    # call_debit_spread|1-3DTE present on both sides.
    cds13 = sb["call_debit_spread|1-3DTE"]
    assert cds13["n_trades_T"] == 1
    assert cds13["pnl_T"] == pytest.approx(20.0)
    assert cds13["n_trades_B"] == 2
    assert cds13["pnl_B"] == pytest.approx(-20.0)
    assert cds13["wins_B"] == 1
    assert cds13["win_rate_B"] == pytest.approx(0.5)


from backtests.intraday_router_wf import aggregate_strategy_bucket


def _win(sb):
    """Wrap a by_strategy_bucket dict into a window_result shape."""
    return {"stats": {"by_strategy_bucket": sb}}


def test_aggregate_strategy_bucket_sums_treatment_across_windows():
    key = "put_debit_spread|1-3DTE"
    windows = [
        _win({key: {"n_trades_T": 2, "pnl_T": 60.0, "wins_T": 1}}),
        _win({key: {"n_trades_T": 3, "pnl_T": -30.0, "wins_T": 1}}),
        # A window missing the key entirely — must be tolerated.
        _win({"call_debit_spread|0DTE": {"n_trades_T": 5, "pnl_T": 10.0, "wins_T": 4}}),
        _win({key: {"n_trades_T": 1, "pnl_T": 90.0, "wins_T": 1}}),
    ]
    agg = aggregate_strategy_bucket(windows, key)
    assert agg["n"] == 6
    assert agg["pnl"] == pytest.approx(120.0)
    assert agg["mean"] == pytest.approx(120.0 / 6)
    assert agg["win_rate"] == pytest.approx(3 / 6)


def test_aggregate_strategy_bucket_absent_everywhere_returns_zeros():
    windows = [
        _win({"iron_condor|0DTE": {"n_trades_T": 4, "pnl_T": 12.0, "wins_T": 3}}),
        _win({}),
    ]
    agg = aggregate_strategy_bucket(windows, "put_debit_spread|1-3DTE")
    assert agg["n"] == 0
    assert agg["pnl"] == 0.0
    assert agg["mean"] == 0.0
    assert agg["win_rate"] == 0.0


from backtests.intraday_router_wf import window_verdict, aggregate_verdict


def test_window_verdict_returns_raw_when_thresholds_unset(monkeypatch):
    """All thresholds None → verdict 'raw' regardless of stats.

    The module's MIN_* were calibrated 2026-06-02 (no longer None), so isolate
    the raw branch by forcing them None here — this tests the logic, not the
    current calibrated values."""
    import backtests.intraday_router_wf as wf
    for name in ("MIN_DELTA_PNL_PER_TRADE", "MIN_OOS_PNL", "MIN_OOS_SHARPE", "MIN_OOS_WIN_RATE"):
        monkeypatch.setattr(wf, name, None)
    stats = {"n_trades_T": 50, "pnl_T": 1000.0, "sharpe_T": 1.5, "win_rate_T": 0.7,
             "delta_pnl_per_trade": 10.0}
    assert window_verdict(stats) == "raw"


def test_window_verdict_pass_fail_with_calibrated_thresholds():
    """With the calibrated thresholds set, a profitable window passes and a
    losing one fails — verifies the verdict is no longer perpetually 'raw'."""
    good = {"n_trades_T": 50, "pnl_T": 1000.0, "sharpe_T": 1.5, "win_rate_T": 0.7,
            "delta_pnl_per_trade": 10.0}
    bad  = {"n_trades_T": 50, "pnl_T": -1000.0, "sharpe_T": -0.5, "win_rate_T": 0.45,
            "delta_pnl_per_trade": -5.0}
    assert window_verdict(good) == "pass"
    assert window_verdict(bad) == "fail"


def test_window_verdict_inconclusive_when_too_few_trades():
    stats = {"n_trades_T": 5, "pnl_T": 1000.0, "sharpe_T": 1.5, "win_rate_T": 0.7,
             "delta_pnl_per_trade": 10.0}
    # Inconclusive even with great stats when n < MIN_N_FOR_VERDICT.
    assert window_verdict(stats, min_n=10) == "inconclusive"


def test_aggregate_verdict_pass_rate():
    """Pass rate excludes 'inconclusive' from the denominator."""
    results = [
        {"verdict": "pass"}, {"verdict": "pass"}, {"verdict": "pass"},
        {"verdict": "fail"},
        {"verdict": "inconclusive"},
    ]
    agg = aggregate_verdict(results)
    assert agg["n_windows"] == 5
    assert agg["n_pass"] == 3
    assert agg["n_fail"] == 1
    assert agg["n_inconclusive"] == 1
    assert agg["pass_rate"] == pytest.approx(3 / 4)   # 3 pass / (3 pass + 1 fail)


def test_aggregate_verdict_all_raw_when_thresholds_unset():
    results = [{"verdict": "raw"}, {"verdict": "raw"}]
    agg = aggregate_verdict(results)
    assert agg["pass_rate"] is None
    assert agg["n_raw"] == 2


from datetime import date as _date
from unittest.mock import MagicMock, patch
from backtests.intraday_router_wf import run_window


def _fake_setup(strategy="iron_condor", conviction="high", score=100,
                direction="neutral"):
    s = MagicMock()
    s.strategy = strategy
    s.conviction = conviction
    s.score = score
    s.direction = direction
    return s


def test_run_window_apples_to_apples_skipped_day():
    """A day that raises in setup-building drops from BOTH treatment and baseline."""

    def get_setup(d):
        if d == _date(2024, 6, 17):
            raise RuntimeError("simulated data failure")
        return [_fake_setup()]

    pnl_log: list = []
    def get_pnl(day, setup, strategy, dte_bucket):
        pnl_log.append((day, strategy, dte_bucket))
        return {"pnl_dollars": 50.0, "strategy": strategy, "dte_bucket": dte_bucket}

    result = run_window(
        train_range=(_date(2024, 1, 1), _date(2024, 6, 14)),
        test_range=(_date(2024, 6, 17), _date(2024, 6, 18)),
        get_setup=get_setup,
        get_pnl=get_pnl,
    )

    # 2024-06-17 was skipped on the T side → it must also drop from B.
    # Apples-to-apples: equal trades on both sides for the remaining day.
    assert result["stats"]["n_trades_T"] == result["stats"]["n_trades_B"]
    # 06-17 skipped + 06-18 traded → only one day contributes per side.
    # IC score 100 hits ULTRA_CONVICTION_DOUBLE_DTE_SCORE → 2 buckets per day.
    assert result["stats"]["n_trades_T"] == 2


def _featured_setup(strategy="put_debit_spread", conviction="high", score=72,
                    direction="bearish", rsi=41.0, rvol=1.8, atr=3.2,
                    trend="down"):
    """A setup with REAL feature values (not MagicMock auto-attrs) so the
    row-collection test can assert exact stamped values."""
    s = MagicMock()
    s.strategy   = strategy
    s.conviction = conviction
    s.score      = score
    s.direction  = direction
    s.rsi        = rsi
    s.rvol       = rvol
    s.atr        = atr
    s.trend      = trend
    return s


def test_run_window_collects_rows_T_with_setup_features_stamped():
    """run_window returns 'rows_T': one flat row per TREATMENT-side trade,
    with the setup's entry features (direction/score/rsi/trend/...) stamped
    alongside the outcome fields (pnl_dollars/outcome/exit_reason)."""

    def get_setup(d):
        return [_featured_setup()]

    def get_pnl(day, setup, strategy, dte_bucket):
        return {
            "date": day.isoformat(),
            "entry_spot": 500.0,
            "entry_px": 1.25,
            "pnl_dollars": -25.0,
            "outcome": "loss",
            "exit_reason": "eod",
            "strategy": strategy,
            "dte_bucket": dte_bucket,
        }

    result = run_window(
        train_range=(_date(2024, 1, 1), _date(2024, 6, 14)),
        test_range=(_date(2024, 6, 17), _date(2024, 6, 17)),  # single day
        get_setup=get_setup,
        get_pnl=get_pnl,
    )

    # Backward compat: existing keys untouched.
    assert "stats" in result and "verdict" in result and "skip_reasons" in result
    assert "rows_T" in result

    rows = result["rows_T"]
    # One row per treatment trade. put_debit_spread is directional, so the
    # router emits one bucket per day here (score 72 < ULTRA double-DTE floor).
    assert len(rows) == result["stats"]["n_trades_T"]
    assert len(rows) >= 1

    row = rows[0]
    # Setup features stamped.
    assert row["strategy"]   == "put_debit_spread"
    assert row["direction"]  == "bearish"
    assert row["score"]      == 72
    assert row["conviction"] == "high"
    assert row["rsi"]        == 41.0
    assert row["rvol"]       == 1.8
    assert row["atr"]        == 3.2
    assert row["trend"]      == "down"
    # Outcome fields present.
    assert row["entry_spot"]  == 500.0
    assert row["entry_px"]    == 1.25
    assert row["pnl_dollars"] == -25.0
    assert row["outcome"]     == "loss"
    assert row["exit_reason"] == "eod"
    assert "dte_bucket" in row and "date" in row


def test_run_window_returns_skip_reasons():
    def get_setup(d):
        return []   # every day yields no setups → all skipped on both sides

    def get_pnl(*a, **kw):
        return {"pnl_dollars": 0.0}

    result = run_window(
        train_range=(_date(2024, 1, 1), _date(2024, 6, 14)),
        test_range=(_date(2024, 6, 17), _date(2024, 6, 18)),
        get_setup=get_setup,
        get_pnl=get_pnl,
    )
    assert result["stats"]["n_trades_T"] == 0
    assert "skip_reasons" in result
    assert result["skip_reasons"]["empty_setup"] >= 2


@pytest.mark.integration
def test_run_walk_forward_smoke_one_window_completes():
    """Smoke-run one short window end-to-end. Confirms the pipeline does
    not crash, produces non-empty stats, and emits a verdict (likely 'raw'
    until thresholds are calibrated).

    Skipped in the default `pytest -m 'not integration'` invocation. Run
    explicitly with `pytest -m integration tests/test_intraday_router_wf.py`.
    """
    from backtests.intraday_router_wf import run_walk_forward

    # Range must cover train + test (3mo + 3mo = 6mo). With train_months=3,
    # test_start anchors to Oct 1, so end must be >= Dec 31 for the window
    # to fit. step_months=3 ensures only one window (next test_start would
    # require Jan-Mar 2025 data, overshooting end).
    report = run_walk_forward(
        date(2024, 7, 1), date(2024, 12, 31),
        train_months=3, test_months=3, step_months=3,
    )
    assert report["aggregate"]["n_windows"] >= 1
    w = report["windows"][0]
    assert "stats" in w
    assert "n_trades_T" in w["stats"]
    assert "n_trades_B" in w["stats"]
    assert w["verdict"] in {"raw", "pass", "fail", "inconclusive"}
