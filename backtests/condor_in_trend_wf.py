"""
backtests/condor_in_trend_wf.py -- Does selling iron condors in a TRENDING
uptrend have an edge, and does it survive out-of-sample?

The question (from the user): we're overextended in trending_up_calm. Instead
of skipping, would an iron condor -- with a stop loss -- be wise here?

This prices THREE strategies on the *exact same* set of trending_up_calm
entry days, using the realistic BS-lifecycle engine (same one the live paper
trader marks with), then splits the trades into an in-sample (early) window
and an out-of-sample (later) window. The split is the whole point: a strategy
that only looks good in-sample and dies OOS was curve-fitting, not edge.

  baseline     : the production play for the regime (bull debit/credit)
  condor       : iron condor, live exit rules, NO hard stop
  condor+stop  : iron condor, closed if loss hits a fraction of max loss

Every trending_up_calm day is priced independently (no concurrency cap) so we
see the full per-trade distribution on identical dates -- the TOTAL column is
therefore gross/overlapping, NOT a real-account sum. Read win% and $/trade
(expectancy) as the verdict; total is context only.

Run:  python -m backtests.condor_in_trend_wf
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from loguru import logger

from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
from backtests.realistic_pricing import simulate_trade, _vix_lookup
from data.event_calendar import EventCalendar
from signals.regime_detector import Regime

STOP_FRACS     = [0.5, 0.8]   # close condor at this fraction of max loss
OOS_FRACTION   = 0.60         # first 60% of entry days = in-sample, rest = OOS


def _price_all(spy, dates, didx, va, entry_dates, play, stop_frac=None):
    """Price `play` on each entry date independently. Returns list of trade dicts."""
    out = []
    for d in entry_dates:
        d = pd.to_datetime(d)
        if d not in didx:
            continue
        r = simulate_trade(spy, dates, didx[d], play, va, stop_loss_frac=stop_frac)
        if r:
            r["date"] = d
            out.append(r)
    return out


def _metrics(trades):
    if not trades:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0, "edge": 0.0}
    pnl = np.array([t["pnl_dollars"] for t in trades], dtype=float)
    wins = (pnl > 0).sum()
    edge = float(np.mean(pnl) / (np.std(pnl) + 1e-9))  # per-trade mean/std (NOT annualised)
    return {"n": len(pnl), "win": round(wins / len(pnl) * 100, 1),
            "avg": round(float(np.mean(pnl)), 1), "total": int(pnl.sum()),
            "edge": round(edge, 3)}


def _split(trades, boundary):
    ins = [t for t in trades if t["date"] < boundary]
    oos = [t for t in trades if t["date"] >= boundary]
    return ins, oos


def _row(label, window, m):
    return (f"  {label:<16} {window:<12} {m['n']:>4} {m['win']:>5.1f}% "
            f"${m['avg']:>+7.0f} {m['edge']:>+7.3f} ${m['total']:>+9,}")


def main():
    loader = BacktestDataLoader()
    spy_df, vix_df = loader.load(years=5, source="local")

    # Regime classification per day (production thresholds).
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()
    regime_df["date"] = pd.to_datetime(regime_df["date"])

    tuc = regime_df[(regime_df["regime"] == Regime.TRENDING_UP_CALM.value) &
                    (regime_df["tradeable"] == True)].sort_values("date")
    entry_dates = list(tuc["date"])
    if not entry_dates:
        print("No tradeable trending_up_calm days in the sample.")
        return

    # in-sample / OOS boundary by entry-day count.
    boundary = entry_dates[int(len(entry_dates) * OOS_FRACTION)]

    # Shared price-walk inputs.
    spy2  = spy_df.copy(); spy2.index = pd.to_datetime(spy2.index)
    dates = sorted(pd.to_datetime(spy2.index))
    didx  = {d: i for i, d in enumerate(dates)}
    va    = _vix_lookup(dates, vix_df)

    # Baseline = whatever production play the regime assigned each day. The
    # plays differ per day, so price them individually then concatenate.
    base_trades = []
    for d, play in zip(tuc["date"], tuc["play"]):
        base_trades += _price_all(spy2, dates, didx, va, [d], play, None)

    strategies = {"baseline": base_trades,
                  "condor (no stop)": _price_all(spy2, dates, didx, va,
                                                 entry_dates, "iron_condor", None)}
    for f in STOP_FRACS:
        strategies[f"condor+stop {int(f*100)}%"] = _price_all(
            spy2, dates, didx, va, entry_dates, "iron_condor", f)

    print("\n" + "=" * 72)
    print("  IRON CONDOR IN trending_up_calm -- IN-SAMPLE vs OUT-OF-SAMPLE")
    print("=" * 72)
    print(f"  trending_up_calm tradeable days: {len(entry_dates)}  "
          f"({entry_dates[0].date()} -> {entry_dates[-1].date()})")
    print(f"  in-sample < {boundary.date()} <= out-of-sample")
    print(f"  stop levels = fraction of the condor's defined max loss")
    print(f"\n  {'strategy':<16} {'window':<12} {'n':>4} {'win%':>6} "
          f"{'$/trade':>8} {'edge':>7} {'total':>10}")

    summary = {}
    for name, trades in strategies.items():
        ins, oos = _split(trades, boundary)
        summary[name] = (_metrics(ins), _metrics(oos))
        print("-" * 72)
        print(_row(name, "in-sample", _metrics(ins)))
        print(_row("",   "out-sample", _metrics(oos)))

    print("=" * 70)
    print("  VERDICT")
    for name, (ins, oos) in summary.items():
        ins_e, oos_e = ins["edge"], oos["edge"]
        if ins_e <= 0:
            note = "no in-sample edge to begin with"
        elif oos_e <= 0:
            note = "in-sample edge COLLAPSES out-of-sample (overfit/regime)"
        else:
            ret = oos_e / ins_e * 100
            note = f"OOS retains {ret:.0f}% of in-sample edge"
        print(f"    {name:<14} IS edge {ins_e:+.3f} -> OOS {oos_e:+.3f}  | {note}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    main()
