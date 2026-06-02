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
