"""
learning/meta_dataset.py -- Build the meta-label training set from the backtest.

One row per tradeable day = build_features(...) + label win in {0,1} from the
realistic per-trade P&L. This is the bootstrap dataset; live recalibration
appends real paper outcomes later (learning/meta_recalibrate.py).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from signals.feature_builder import build_features


def label_from_pnl(pnl: float) -> int:
    """Win label: strictly positive realized P&L."""
    return 1 if pnl > 0 else 0


def assemble_dataset(regime_df: pd.DataFrame, trades_df: pd.DataFrame,
                     spy_df: pd.DataFrame | None = None,
                     include_fvg: bool = False) -> pd.DataFrame:
    """Inner-join regime rows with realistic trade outcomes on date -> labeled rows."""
    spy_idx = None
    if spy_df is not None:
        spy_idx = spy_df.copy()
        spy_idx.index = pd.to_datetime(spy_idx.index)

    pnl_by_date = {pd.Timestamp(r["date"]): r["pnl_dollars"]
                   for _, r in trades_df.iterrows()}
    rows = []
    for _, r in regime_df[regime_df["tradeable"] == True].iterrows():
        d = pd.Timestamp(r["date"])
        if d not in pnl_by_date:
            continue
        metrics = {"adx": r["adx"], "vix": r["vix"], "ivr": r["ivr"],
                   "ma200_dist_%": r["ma200_dist"], "spy_close": r.get("spy_close", 0.0)}
        slice_df = None
        if spy_idx is not None:
            slice_df = spy_idx.loc[:d].tail(60)
        feats = build_features(r["regime"], metrics, slice_df, include_fvg)
        feats["date"] = d
        feats["win"] = label_from_pnl(pnl_by_date[d])
        rows.append(feats)
    return pd.DataFrame(rows)


def build_from_history(years: int = 5, include_fvg: bool = False) -> pd.DataFrame:
    """Convenience: load 5yr local data, run both engines, assemble. Used by trainer."""
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from backtests.realistic_pricing import run_realistic_backtest
    from data.event_calendar import EventCalendar

    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=years).run()
    # spy_close column for FVG/feature use (regime_df already has adx/vix/ivr/ma200_dist).
    if "spy_close" not in regime_df.columns:
        closes = {pd.Timestamp(d): float(spy_df.loc[d, "close"]) for d in spy_df.index}
        regime_df["spy_close"] = regime_df["date"].map(lambda x: closes.get(pd.Timestamp(x), 0.0))
    trades_df = run_realistic_backtest(spy_df, regime_df, vix_df, max_concurrent=9999)
    return assemble_dataset(regime_df, trades_df, spy_df, include_fvg)
