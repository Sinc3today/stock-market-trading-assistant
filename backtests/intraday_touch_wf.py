"""
backtests/intraday_touch_wf.py -- Walk-forward comparison: intraday-touch exit
vs daily-close exit on the realistic-priced SPY backtest population.

Runs simulate_trade twice on identical entry days (touch off, touch on),
joins per-trade outcomes on date, splits 60/40 by entry date (early =
in-sample, late = out-of-sample), and aggregates Δ$/trade, attribution %, and
per-regime breakdown. A later task adds the six-preset verdict matrix on top.

Run:  python -m backtests.intraday_touch_wf
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger


def split_oos(trades: pd.DataFrame, fraction: float = 0.6) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split trades chronologically: first `fraction` = in-sample, rest = OOS."""
    df = trades.sort_values("date").reset_index(drop=True)
    cut = int(len(df) * fraction)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def compare_runs(trades_off: pd.DataFrame, trades_on: pd.DataFrame,
                 oos_fraction: float = 0.6) -> dict:
    """Compute Δ$/trade, attribution %, IS/OOS, per-regime breakdown.

    trades_off / trades_on each have columns: date, regime, pnl_dollars,
    exit_reason. They share entry dates 1:1; we inner-join on date.
    """
    off = trades_off.set_index("date")[["pnl_dollars", "regime", "exit_reason"]].rename(
        columns={"pnl_dollars": "pnl_off", "exit_reason": "reason_off"})
    on = trades_on.set_index("date")[["pnl_dollars", "exit_reason"]].rename(
        columns={"pnl_dollars": "pnl_on", "exit_reason": "reason_on"})
    j = off.join(on, how="inner").reset_index()
    j["delta"] = j["pnl_on"] - j["pnl_off"]

    ins, oos = split_oos(j, fraction=oos_fraction)
    attribution = float((oos["reason_on"] == "target_intraday").mean()) if len(oos) else 0.0

    per_regime = {}
    for r in sorted(j["regime"].unique()):
        sub = j[j["regime"] == r]
        per_regime[r] = {"n": int(len(sub)),
                         "delta_per_trade": float(sub["delta"].mean()) if len(sub) else 0.0}

    return {
        "n_total":                int(len(j)),
        "n_is":                   int(len(ins)),
        "n_oos":                  int(len(oos)),
        "is_delta_per_trade":     float(ins["delta"].mean()) if len(ins) else 0.0,
        "oos_delta_per_trade":    float(oos["delta"].mean()) if len(oos) else 0.0,
        "oos_baseline_per_trade": float(oos["pnl_off"].mean()) if len(oos) else 0.0,
        "oos_attribution":        attribution,
        "per_regime":             per_regime,
    }


def _price_population(spy_df, vix_df, regime_df, intraday_touch: bool) -> pd.DataFrame:
    """Price every tradeable regime day with the given mode; return per-trade rows."""
    from backtests.realistic_pricing import simulate_trade, _vix_lookup

    spy = spy_df.copy(); spy.index = pd.to_datetime(spy.index)
    dates = sorted(pd.to_datetime(spy.index))
    didx  = {d: i for i, d in enumerate(dates)}
    va    = _vix_lookup(dates, vix_df)

    rows = []
    for _, r in regime_df[regime_df["tradeable"] == True].iterrows():
        d = pd.to_datetime(r["date"])
        if d not in didx:
            continue
        play = r["play"]
        if play == "skip":
            continue
        out = simulate_trade(spy, dates, didx[d], play, va, intraday_touch=intraday_touch)
        if out is None:
            continue
        out["date"]   = d
        out["regime"] = r["regime"]
        rows.append(out)
    return pd.DataFrame(rows)


def run() -> dict:
    """Load 5yr data, price both modes, return the comparison dict. (No printing.)"""
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar

    spy_df, vix_df = BacktestDataLoader().load(years=5, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()

    logger.info("Pricing daily-close mode (touch off)...")
    off = _price_population(spy_df, vix_df, regime_df, intraday_touch=False)
    logger.info(f"  {len(off)} trades")
    logger.info("Pricing intraday-touch mode (touch on)...")
    on  = _price_population(spy_df, vix_df, regime_df, intraday_touch=True)
    logger.info(f"  {len(on)} trades")

    return compare_runs(off, on)


def main():
    result = run()
    # Verdict-matrix printing is added in a later task. For now, print the raw metrics.
    print(result)


if __name__ == "__main__":
    main()
