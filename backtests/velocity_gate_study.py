"""backtests/velocity_gate_study.py -- does a big RECENT move break the condor?

User insight (2026-08-05): the classifier read a +5.7%-in-4-days rally as
"choppy_low_vol — sell premium" because VIX (implied vol) was low and ADX low.
But a fast directional thrust is realized-vol/momentum the condor's short side
feels. Question: in the condor's home regime (choppy_low_vol), do condors opened
right after a large trailing move underperform quiet-entry condors? If yes, a
realized-VELOCITY gate ("SPY moved >X% in N days -> be wary / stand aside") is a
backtestable, defensive improvement — exactly the situation that just burned the
live condors. If no, the move-then-mean-revert tendency actually helps, and we
leave the classifier alone.

Metric: trailing 5-day ABSOLUTE move |close/close[-5]-1| (magnitude, either
direction — a big move up threatens the call side, down the put side). Condor is
re-centered on the current spot each day, so this tests momentum/whipsaw AFTER
the move, not mis-centering. Machinery/honesty = the other studies.
Doc: docs/VELOCITY_GATE_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
import backtests.dte_ladder_study as ladder

DTE = 45              # the live daily-play DTE (what got hurt)
LOOKBACK = 5          # trailing sessions for the velocity metric


def _row(name, rows):
    if len(rows) < 30:
        print(f"{name:>16}{len(rows):>6}   (n<30 — skip)")
        return None
    p = [r["pnl"] for r in rows]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    ao = sum(old) / len(old) if old else float("nan")
    an = sum(new) / len(new) if new else float("nan")
    both = bool(old and new and ao > 0 and an > 0)
    print(f"{name:>16}{len(p):>6}{sum(1 for x in p if x>0)/len(p)*100:>6.0f}%"
          f"{sum(p)/len(p):>9.2f}{min(p):>8.0f}  {ao:>9.2f}{an:>8.2f}  "
          f"{'PASS' if both else 'fail-OOS'}")
    return sum(p) / len(p)


def run(hurt: float = 0.0):
    tag = "  (10% haircut)" if hurt else ""
    print(f"=== {DTE}DTE condor in choppy_low_vol, by trailing {LOOKBACK}d move{tag} ===")
    df = add_features(load())
    df["mv"] = (df["close"] / df["close"].shift(LOOKBACK) - 1).abs() * 100
    df = df[df.index.year >= 2018]
    days = [i for i in range(len(df) - 2)
            if (not bool(df["regime"].iloc[i]))
            and float(df["adx"].iloc[i]) < 32.0
            and float(df["vix"].iloc[i]) < 18.0
            and not pd.isna(df["mv"].iloc[i])]

    orig = ladder.build_legs
    if hurt:
        def hc(structure, spot, sigma, dte):
            built = orig(structure, spot, sigma, dte)
            if built is None:
                return None
            legs, nd, mp, ml = built
            if nd < 0:
                credit = -nd * (1 - hurt)
                return legs, -credit, credit * 100, (5.0 - credit) * 100
            return built
        ladder.build_legs = hc

    buckets = {"quiet <1.5%": [], "1.5-3%": [], "3-5%": [], "fast >5%": []}
    def bkt(m):
        return ("quiet <1.5%" if m < 1.5 else "1.5-3%" if m < 3
                else "3-5%" if m < 5 else "fast >5%")
    try:
        for i in days:
            r = ladder.simulate(df, i, "condor", DTE)
            if r is None:
                continue
            buckets[bkt(float(df["mv"].iloc[i]))].append(
                {"pnl": r["pnl"],
                 "era": "old" if df.index[i].year <= 2022 else "new"})
    finally:
        ladder.build_legs = orig

    print(f"{'trailing move':>16}{'n':>6}{'win%':>7}{'avg':>9}{'worst':>8}"
          f"  {'18-22':>9}{'23+':>8}  verdict")
    avgs = {}
    for k in ("quiet <1.5%", "1.5-3%", "3-5%", "fast >5%"):
        avgs[k] = _row(k, buckets[k])
    return avgs


if __name__ == "__main__":
    a0 = run()
    print()
    a1 = run(hurt=0.10)
    print("\n---- does the velocity gate earn its keep? ----")
    q = a1.get("quiet <1.5%"); f = a1.get("fast >5%")
    if q is not None and f is not None:
        print(f"  quiet-entry avg {q:+.2f}/trade vs fast-entry avg {f:+.2f}/trade "
              f"(haircut). Gap = {q - f:+.2f}.")
