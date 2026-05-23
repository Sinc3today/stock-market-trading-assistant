"""Phase 2a: shared walk-forward primitive — chronological 60/40 split +
per-slice metrics. Future harnesses opt in; existing ones unchanged."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from backtests.wf_common import (
    OOS_FRACTION_DEFAULT, IS_FRACTION_DEFAULT,
    split_oos, metrics_block,
)


def _frame(n=200):
    """A backtest-results-shaped frame: date, tradeable, outcome, pnl.

    Outcome ratio: 60% wins, 30% losses, 10% breakevens (scales with n).
    At n=200: 120 wins, 60 losses, 20 breakevens.
    """
    assert n % 10 == 0, "_frame n must be a multiple of 10"
    wins       = n * 6 // 10   # 60%
    losses     = n * 3 // 10   # 30%
    breakevens = n - wins - losses  # remainder = 10%
    d0 = pd.Timestamp("2022-01-01")
    return pd.DataFrame({
        "date":      [d0 + pd.Timedelta(days=i) for i in range(n)],
        "tradeable": [True] * n,
        "outcome":   (["win"] * wins) + (["loss"] * losses) + (["breakeven"] * breakevens),
        "pnl":       ([120] * wins) + ([-100] * losses) + ([0] * breakevens),
    })


def test_default_fractions_are_60_40():
    """The codebase convention: IS = first 60% of dates, OOS = last 40%."""
    assert IS_FRACTION_DEFAULT  == 0.60
    assert OOS_FRACTION_DEFAULT == 0.40
    # And they sum to 1 (no gap, no overlap).
    assert IS_FRACTION_DEFAULT + OOS_FRACTION_DEFAULT == 1.0


def test_split_oos_returns_chronological_split():
    df = _frame(n=200)
    ins, oos = split_oos(df)
    assert len(ins) == 120          # 60% of 200
    assert len(oos) == 80           # 40% of 200
    # Chronological: every IS date is before every OOS date.
    assert ins["date"].max() < oos["date"].min()


def test_split_oos_respects_custom_in_sample_fraction():
    df = _frame(n=100)
    ins, oos = split_oos(df, in_sample_fraction=0.70)
    assert len(ins) == 70
    assert len(oos) == 30


def test_split_oos_uses_custom_date_column():
    df = pd.DataFrame({
        "entry_date": pd.date_range("2025-01-01", periods=10),
        "tradeable":  [True] * 10,
        "outcome":    ["win"] * 10,
        "pnl":        [100] * 10,
    })
    ins, oos = split_oos(df, date_col="entry_date")
    assert len(ins) == 6 and len(oos) == 4


def test_metrics_block_computes_trades_winrate_pnl_sharpe():
    df = _frame(n=200)
    m = metrics_block(df)
    assert m["trades"]   == 200       # 120 wins + 60 losses + 20 breakevens
    assert m["win_rate"] == 60.0      # 120 / 200
    assert m["pnl"]      == 120 * 120 + 60 * -100 + 20 * 0      # 14400 - 6000 = 8400
    assert m["sharpe"]   > 0.0        # rising avg pnl → positive sharpe


def test_metrics_block_handles_empty_slice():
    df = pd.DataFrame({"date": [], "tradeable": [], "outcome": [], "pnl": []})
    m = metrics_block(df)
    assert m == {"trades": 0, "win_rate": 0.0, "pnl": 0, "sharpe": 0.0}


def test_metrics_block_ignores_non_tradeable_rows():
    """skip days (tradeable=False) shouldn't count in the metrics."""
    df = pd.DataFrame({
        "date":      pd.date_range("2025-01-01", periods=10),
        "tradeable": [True, True, False, False, True, True, False, True, True, True],
        "outcome":   ["win"]*2 + ["skip"]*2 + ["loss"]*2 + ["skip"] + ["win"]*3,
        "pnl":       [100, 100, 0, 0, -50, -50, 0, 100, 100, 100],
    })
    m = metrics_block(df)
    assert m["trades"] == 7                                    # 5 wins + 2 losses
    assert m["win_rate"] == round(5 / 7 * 100, 1)
    assert m["pnl"] == 100*5 + (-50)*2                          # 500 - 100 = 400


def test_split_then_metrics_workflow():
    """End-to-end: split data 60/40, compute metrics on each slice."""
    df = _frame(n=200)
    ins, oos = split_oos(df)
    m_is  = metrics_block(ins)
    m_oos = metrics_block(oos)
    # Both slices have non-zero stats (the synthetic frame has trades in both).
    assert m_is["trades"]  == 120
    assert m_oos["trades"] == 80
    # PnL aggregates to the whole frame's pnl
    assert m_is["pnl"] + m_oos["pnl"] == metrics_block(df)["pnl"]
