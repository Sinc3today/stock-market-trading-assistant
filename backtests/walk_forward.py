"""
backtests/walk_forward.py -- No-lookahead threshold validation.

Today's tuning (ADX_TREND_MIN, EXTENDED_TREND_MAX_PCT, VIX_CALM_MAX) was
optimized on the WHOLE 2022-2026 dataset -- in-sample. That's the classic
overfitting trap: numbers that look great on the data you tuned on and fall
apart on data you didn't.

Walk-forward answers "does the tuning generalize?" honestly:

    for each fold:
        pick the best thresholds on the TRAIN window (past only)
        apply them to the TEST window (the next, UNSEEN slice)
        record the TEST performance

Aggregate test-window performance is the out-of-sample (OOS) result. If OOS
holds up near the in-sample number, the edge is real; if it collapses, we
were curve-fitting.

Uses the fixed-payoff SPYBacktest for the grid search -- it's fast and valid
for *ranking* configs (which is all tuning needs). Realistic absolute P&L is
the realistic_pricing engine's job; this is about whether the threshold
CHOICE survives forward.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from loguru import logger

import signals.regime_detector as rd
from backtests.spy_daily_backtest import SPYBacktest

# Grid searched in each train window. Kept small + sensible (mechanical
# rationale, not a fishing expedition over hundreds of combos).
ADX_GRID  = [25, 28, 30, 32]
EXT_GRID  = [8.0, 9.0, 10.0, 12.0]
VIX_CALM  = 18.0


def _metrics(df: pd.DataFrame) -> dict:
    """Sharpe / win / pnl / n over the tradeable rows of a results slice."""
    t = df[df["tradeable"] == True]
    closed = t[t["outcome"].isin(["win", "loss", "breakeven"])]
    n = len(closed)
    if n == 0:
        return {"n": 0, "win": 0.0, "pnl": 0, "sharpe": 0.0}
    wins = (closed["outcome"] == "win").sum()
    d = t["pnl"].values
    sharpe = (np.mean(d) / (np.std(d) + 1e-9)) * np.sqrt(252) if len(d) > 1 else 0.0
    return {"n": n, "win": round(wins / n * 100, 1),
            "pnl": int(t["pnl"].sum()), "sharpe": round(float(sharpe), 2)}


def _run_config(spy_df, vix_df, event_cal, adx: float, ext: float,
                vix_calm: float = VIX_CALM) -> pd.DataFrame:
    """Run the fixed-payoff backtest under one threshold config. Restores
    globals afterward."""
    o_adx, o_vix, o_ext = rd.ADX_TREND_MIN, rd.VIX_CALM_MAX, rd.EXTENDED_TREND_MAX_PCT
    try:
        rd.ADX_TREND_MIN          = adx
        rd.VIX_CALM_MAX           = vix_calm
        rd.EXTENDED_TREND_MAX_PCT = ext
        return SPYBacktest(spy_df, vix_df, event_cal, years=5).run()
    finally:
        rd.ADX_TREND_MIN, rd.VIX_CALM_MAX, rd.EXTENDED_TREND_MAX_PCT = o_adx, o_vix, o_ext


def walk_forward(spy_df, vix_df, event_cal,
                 fold_years: list[int] | None = None,
                 optimize: str = "sharpe") -> dict:
    """
    Expanding-window walk-forward. For each fold boundary year Y: train on
    everything before Jan-1-Y, pick the grid config that maximises `optimize`
    on that train slice, then measure it on year Y (the unseen test slice).

    Returns {"folds": [...], "oos": {...}, "in_sample": {...}}.
    """
    # Run every grid config once over the full history (do NOT mutate the
    # input frames -- SPYBacktest compares its own date types internally).
    cache: dict[tuple, pd.DataFrame] = {}
    for adx in ADX_GRID:
        for ext in EXT_GRID:
            df = _run_config(spy_df, vix_df, event_cal, adx, ext)
            df["date"] = pd.to_datetime(df["date"])
            cache[(adx, ext)] = df

    any_df = next(iter(cache.values()))
    years  = sorted({d.year for d in any_df["date"]})
    if fold_years is None:
        fold_years = years[1:]   # test each year after the first

    folds = []
    oos_frames = []
    for test_year in fold_years:
        # pick best config on TRAIN (strictly before the test year)
        best, best_score = None, -1e18
        for cfg, df in cache.items():
            train = df[df["date"].dt.year < test_year]
            if train[train["tradeable"] == True].shape[0] < 20:
                continue
            score = _metrics(train)[optimize]
            if score > best_score:
                best_score, best = score, cfg
        if best is None:
            continue
        test_df = cache[best][cache[best]["date"].dt.year == test_year]
        tm = _metrics(test_df)
        folds.append({"test_year": test_year, "chosen_adx": best[0],
                      "chosen_ext": best[1], "test": tm})
        oos_frames.append(test_df)

    oos = _metrics(pd.concat(oos_frames)) if oos_frames else {"n": 0}
    # In-sample reference: the production config tuned on the whole period.
    in_sample = _metrics(_run_config(spy_df, vix_df, event_cal,
                                     rd.ADX_TREND_MIN, rd.EXTENDED_TREND_MAX_PCT))
    return {"folds": folds, "oos": oos, "in_sample": in_sample}


def print_report(result: dict):
    print("\n" + "=" * 64)
    print("  WALK-FORWARD VALIDATION (out-of-sample)")
    print("=" * 64)
    print(f"  {'TestYr':>6} {'ADX':>4} {'Ext%':>5} {'Trades':>7} {'Win%':>6} {'P&L':>9} {'Sharpe':>7}")
    for f in result["folds"]:
        t = f["test"]
        print(f"  {f['test_year']:>6} {f['chosen_adx']:>4} {f['chosen_ext']:>5} "
              f"{t['n']:>7} {t['win']:>5.1f}% ${t['pnl']:>+8,} {t['sharpe']:>7.2f}")
    o, i = result["oos"], result["in_sample"]
    print("-" * 64)
    print(f"  {'OOS agg':>6} {'':>4} {'':>5} {o.get('n',0):>7} {o.get('win',0):>5.1f}% "
          f"${o.get('pnl',0):>+8,} {o.get('sharpe',0):>7.2f}")
    print(f"  {'In-samp':>6} {'':>4} {'':>5} {i.get('n',0):>7} {i.get('win',0):>5.1f}% "
          f"${i.get('pnl',0):>+8,} {i.get('sharpe',0):>7.2f}")
    print("=" * 64)
    o_sh, i_sh = o.get("sharpe", 0), i.get("sharpe", 0)
    if i_sh:
        retention = o_sh / i_sh * 100
        verdict = ("edge GENERALISES" if retention >= 70 else
                   "PARTIAL — some overfit" if retention >= 40 else
                   "OVERFIT — OOS collapses")
        print(f"  OOS retains {retention:.0f}% of in-sample Sharpe → {verdict}")
    print()


if __name__ == "__main__":
    from backtests.spy_daily_backtest import BacktestDataLoader
    from data.event_calendar import EventCalendar
    loader = BacktestDataLoader()
    spy, vix = loader.load(years=5, source="local")
    print_report(walk_forward(spy, vix, EventCalendar()))
