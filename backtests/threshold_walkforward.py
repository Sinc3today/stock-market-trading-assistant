"""backtests/threshold_walkforward.py -- are ADX 32 / VIX 18 curve-fit?

Audit T2#7: the regime thresholds were reached via sequential bumps + an
in-sample grid search (the tuner has no OOS split). Proper check: rolling
walk-forward — tune the (ADX, VIX) condor gate on each fold, test on the NEXT
fold, and compare against the fixed live pair (32, 18) on the same OOS data.
If per-fold winners bounce around and the fixed pair beats/matches them OOS,
the exact values matter less than the neighborhood; if the fixed pair LOSES
badly OOS, it's curve-fit.

Approach: one classification pass captures per-day (adx, vix); a threshold pair
is then a pure filter (condor day = adx < A and vix < V), and each candidate
set of entry days runs through the same condor sim as the live backtest.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.structure_comparison import condor_legs, simulate
from signals.regime_detector import RegimeDetector

ADX_GRID = [26, 28, 30, 32, 34, 36]
VIX_GRID = [16, 17, 18, 19, 20]
LIVE = (32.0, 18.0)


def daily_metrics(spy_df, vix_at):
    """(date_index, adx, vix) per classifiable day — single pass."""
    detector = RegimeDetector()
    dates = sorted(spy_df.index)
    rows = []
    for i, d in enumerate(dates):
        if i < 210 or i > len(dates) - 35:
            continue
        hist = spy_df.loc[dates[max(0, i - 250):i + 1]].copy()
        hist.index = pd.to_datetime(hist.index)
        try:
            r = detector.classify(spy_daily_df=hist, vix_current=vix_at.get(d, 16.0),
                                  ivr_current=30.0, today=d)
        except Exception:
            continue
        rows.append({"i": i, "date": d, "adx": float(r.metrics.get("adx", 0)),
                     "vix": vix_at.get(d, 16.0)})
    return dates, rows


def pnl_for_pair(rows, lo, hi, adx_max, vix_max, spy_df, dates, vix_at):
    """Total condor P&L for entry days in rows[lo:hi] passing the gate."""
    total, n = 0.0, 0
    for r in rows[lo:hi]:
        if r["adx"] < adx_max and r["vix"] < vix_max:
            spot = float(spy_df.loc[dates[r["i"]], "close"])
            res = simulate(condor_legs(spot, 0.020), spy_df, dates, r["i"], vix_at)
            if res:
                total += res["pnl"]; n += 1
    return total, n


def run(years=5, folds=4):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()} \
        if vix_df is not None and len(vix_df) else {}
    dates, rows = daily_metrics(spy_df, vix_at)
    print(f"classifiable days: {len(rows)}")

    size = len(rows) // folds
    print(f"\n{'fold':>5}{'IS-best (A,V)':>16}{'IS P&L':>9}{'OOS w/best':>12}{'OOS w/live(32,18)':>19}")
    agree = 0
    for k in range(folds - 1):
        is_lo, is_hi = k * size, (k + 1) * size
        oos_lo, oos_hi = (k + 1) * size, (k + 2) * size
        best, best_pnl = None, -1e18
        for a in ADX_GRID:
            for v in VIX_GRID:
                p, n = pnl_for_pair(rows, is_lo, is_hi, a, v, spy_df, dates, vix_at)
                if n >= 10 and p > best_pnl:
                    best, best_pnl = (a, v), p
        oos_best, nb = pnl_for_pair(rows, oos_lo, oos_hi, *best, spy_df, dates, vix_at) \
            if best else (0, 0)
        oos_live, nl = pnl_for_pair(rows, oos_lo, oos_hi, *LIVE, spy_df, dates, vix_at)
        if best == tuple(int(x) for x in LIVE):
            agree += 1
        print(f"{k+1:>5}{str(best):>16}{best_pnl:>9.0f}{oos_best:>9.0f} n={nb:<3}"
              f"{oos_live:>12.0f} n={nl}")
    print(f"\nfolds where IS-best == live pair: {agree}/{folds-1}")


if __name__ == "__main__":
    run()
