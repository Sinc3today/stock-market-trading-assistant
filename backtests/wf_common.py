"""
backtests/wf_common.py -- Shared walk-forward primitive for backtest harnesses.

Every walk-forward harness in this codebase does some variant of "split the
results chronologically by date, compute {trades, win_rate, pnl, sharpe} on
each slice, compare IS vs OOS." This module extracts that into one place so
future per-sub-strategy harnesses don't re-implement it (and gradually drift).

Convention used codebase-wide:
    IS  = first  IS_FRACTION_DEFAULT  (= 60%) of dates by chronological order
    OOS = last   OOS_FRACTION_DEFAULT (= 40%) of dates by chronological order

Existing harnesses (walk_forward.py, condor_in_trend_wf.py, intraday_touch_wf.py,
meta_trainer.passes_ship_bar, hypothesis_runner._default_backtest) currently
re-implement these primitives inline; they keep working as-is. New harnesses
should import from here; existing ones can migrate lazily.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

IS_FRACTION_DEFAULT  = 0.60
OOS_FRACTION_DEFAULT = 0.40


def split_oos(df: pd.DataFrame,
              in_sample_fraction: float = IS_FRACTION_DEFAULT,
              date_col: str = "date") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split: returns (in_sample_slice, oos_slice).

    The split is by ROW COUNT after sorting by date_col — the first
    `in_sample_fraction` of rows is in-sample, the rest is out-of-sample.
    Both slices preserve every column of the input frame.
    """
    out = df.copy()
    if date_col in out.columns:
        out[date_col] = pd.to_datetime(out[date_col])
        out = out.sort_values(date_col).reset_index(drop=True)
    cut = int(len(out) * in_sample_fraction)
    return out.iloc[:cut].copy(), out.iloc[cut:].copy()


def metrics_block(df: pd.DataFrame) -> dict:
    """Compute {trades, win_rate, pnl, sharpe} for a slice of backtest rows.

    Expects columns: tradeable (bool), outcome (str in {win,loss,breakeven,skip}),
    pnl (numeric). Rows with tradeable=False are excluded from all stats.
    Empty input returns the zero block (no division by zero).
    """
    if len(df) == 0:
        return {"trades": 0, "win_rate": 0.0, "pnl": 0, "sharpe": 0.0}

    traded = df[df["tradeable"] == True]
    closed = traded[traded["outcome"].isin(["win", "loss", "breakeven"])]
    n      = len(closed)
    wins   = len(closed[closed["outcome"] == "win"])
    wr     = round(wins / n * 100, 1) if n else 0.0
    pnl    = int(traded["pnl"].sum()) if len(traded) else 0
    daily  = traded["pnl"].values
    sharpe = (float((np.mean(daily) / (np.std(daily) + 1e-9)) * np.sqrt(252))
              if len(daily) > 1 else 0.0)
    return {
        "trades":   int(n),
        "win_rate": wr,
        "pnl":      pnl,
        "sharpe":   round(sharpe, 3),
    }
