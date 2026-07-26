"""backtests/ivr_gate_study.py -- is the IV-Rank>=50 condor gate justified?

Finding (2026-07-26): OptionsLayer rejects a neutral (choppy) condor unless
IV Rank >= 50 (IV_RANK_HIGH). Current IVR ~29, and it hasn't cleared 50 in
weeks, so the bot has stood down on every choppy_low_vol day — its home regime.

Question: do LOW-IVR condors actually lack edge (gate justified), or is the gate
suppressing profitable trades (gate too tight)? Our DTE-ladder study traded these
same days by ADX<32 & VIX<18 with NO IVR filter and found 82% win — so this
isolates IVR as the variable.

Method (same honesty rules): SPY+VIX 2018-present, choppy_low_vol days
(not-trending & VIX<18), 45DTE condor (the live daily-play DTE the gate governs),
managed 70%/21-DTE. IV Rank = trailing-252d percentile of VIX (the live proxy
definition). Bucketed by IVR; OOS era split (2018-22 vs 2023+, both must be
positive); 10% fill haircut. Doc verdict printed at the end.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
import backtests.dte_ladder_study as ladder

DTE = 45
GATE = 50            # IV_RANK_HIGH — the live cutoff under scrutiny


def rolling_ivr(vix: pd.Series, window: int = 252) -> pd.Series:
    lo = vix.rolling(window, min_periods=60).min()
    hi = vix.rolling(window, min_periods=60).max()
    return (vix - lo) / (hi - lo) * 100


def _bucket(ivr: float) -> str:
    if ivr < 25:  return "IVR <25"
    if ivr < 50:  return "IVR 25-50"
    if ivr < 75:  return "IVR 50-75"
    return "IVR 75+"


def run(hurt: float = 0.0):
    tag = "  (10% fill haircut)" if hurt else ""
    print(f"=== 45DTE condor in choppy_low_vol, by IV Rank{tag} ===")
    df = add_features(load())
    df["ivr"] = rolling_ivr(df["vix"])
    df = df[df.index.year >= 2018]
    days = [i for i in range(len(df) - 2)
            if (not bool(df["regime"].iloc[i]))
            and float(df["adx"].iloc[i]) < 32.0
            and float(df["vix"].iloc[i]) < 18.0
            and not pd.isna(df["ivr"].iloc[i])]

    # haircut by swapping the ladder's build_legs (same trick as run_haircut)
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

    order = ["IVR <25", "IVR 25-50", "IVR 50-75", "IVR 75+"]
    buckets = {k: [] for k in order}
    below = []   # IVR < GATE (what the live gate BLOCKS)
    above = []   # IVR >= GATE (what the live gate ALLOWS)
    try:
        for i in days:
            r = ladder.simulate(df, i, "condor", DTE)
            if r is None:
                continue
            ivr = float(df["ivr"].iloc[i])
            era = "old" if df.index[i].year <= 2022 else "new"
            rec = {"pnl": r["pnl"], "era": era}
            buckets[_bucket(ivr)].append(rec)
            (below if ivr < GATE else above).append(rec)
    finally:
        ladder.build_legs = orig

    print(f"{'bucket':>12}{'n':>6}{'win%':>7}{'avg':>9}{'total':>10}{'worst':>8}"
          f"  {'18-22':>9}{'23+':>8}  verdict")
    for k in order:
        _row(k, buckets[k])
    print("  " + "-" * 66)
    _row("BLOCKED (<50)", below)
    _row("ALLOWED (>=50)", above)


def _row(name, rows):
    if len(rows) < 30:
        print(f"{name:>14}{len(rows):>6}   (n<30 — skip)")
        return
    p = [r["pnl"] for r in rows]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    avg_o = sum(old) / len(old) if old else float("nan")
    avg_n = sum(new) / len(new) if new else float("nan")
    both_pos = bool(old and new and avg_o > 0 and avg_n > 0)
    print(f"{name:>14}{len(p):>6}{sum(1 for x in p if x>0)/len(p)*100:>6.0f}%"
          f"{sum(p)/len(p):>9.2f}{sum(p):>10.0f}{min(p):>8.0f}  "
          f"{avg_o:>9.2f}{avg_n:>8.2f}  {'PASS' if both_pos else 'fail-OOS'}")


if __name__ == "__main__":
    run()
    print()
    run(hurt=0.10)
