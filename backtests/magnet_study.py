"""backtests/magnet_study.py -- do 'magnet zones' exist, and do they pay?

User intuition (2026-07-15): "trading away from magnet zones" — price gets
pulled back to certain levels, so sell premium centered on the magnet.

HONESTY CONSTRAINT: the classic options magnets (max pain, GEX flip, OI
walls) need HISTORICAL open interest, which none of our sources provide
retroactively. Those stay untestable until the forward wall-snapshot log
(started 2026-07-15) accumulates. What we CAN test with 8.5 years of price
data:

  A. Is the 20-day mean a magnet in chop?  After price deviates X% from
     MA20, what does the next 5 sessions' pull-back look like — vs the same
     measure in trending regimes (where the magnet should NOT hold)?
  B. Does entering the 7DTE condor NEAR the magnet (|spot-MA20| small) beat
     entering stretched-from-magnet? (The condor is centered on spot — if
     spot is far from a magnet that price then reverts to, one short strike
     starts closer to danger.)
  C. OPEX pinning: is the OPEX week's realized range compressed vs ordinary
     weeks (dealer-hedging pin), and do 7DTE condors entered the Monday of
     OPEX week outperform other Mondays?

Regime days + condor simulation reuse the ladder-study machinery.
Doc: docs/MAGNET_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
from backtests.dte_ladder_study import simulate


def third_friday(year: int, month: int):
    from datetime import date, timedelta
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def part_a_ma20_magnet(df):
    print("== A. is MA20 a magnet? (5-session forward move after deviation) ==")
    print(f"{'regime':>18}{'deviation':>12}{'n':>6}{'fwd 5d ret':>12}{'reverts%':>10}")
    fwd = df["close"].shift(-5) / df["close"] - 1
    for regime, mask in (
        ("choppy_low_vol", (~df["regime"]) & (df["adx"] < 32) & (df["vix"] < 18)),
        ("trending_up_calm", df["regime"]),
    ):
        for lo, hi, label in ((-99, -1.5, "<-1.5%"), (-1.5, -0.5, "-1.5..-0.5%"),
                              (-0.5, 0.5, "near magnet"), (0.5, 1.5, "+0.5..1.5%"),
                              (1.5, 99, ">+1.5%")):
            m = mask & (df["ma20_dist"] > lo) & (df["ma20_dist"] <= hi)
            f = fwd[m].dropna()
            if len(f) < 30:
                continue
            # "reverts" = forward move is TOWARD the MA20 (opposite sign of deviation)
            dev = df["ma20_dist"][m].reindex(f.index)
            rev = ((dev > 0) & (f < 0)) | ((dev < 0) & (f > 0)) | (dev.abs() <= 0.5)
            print(f"{regime:>18}{label:>12}{len(f):>6}{f.mean()*100:>11.2f}%"
                  f"{rev.mean()*100:>9.0f}%")


def part_b_condor_vs_magnet_distance(df):
    print("\n== B. 7DTE condor in chop, by |spot - MA20| at entry ==")
    print(f"{'bucket':>14}{'n':>6}{'win%':>7}{'avg':>9}{'worst':>8}")
    days = [i for i in range(len(df) - 2)
            if (not bool(df["regime"].iloc[i]))
            and float(df["adx"].iloc[i]) < 32.0 and float(df["vix"].iloc[i]) < 18.0
            and df.index[i].year >= 2018]
    buckets = {}
    for i in days:
        r = simulate(df, i, "condor", 7)
        if r is None:
            continue
        d = abs(float(df["ma20_dist"].iloc[i]))
        key = "on magnet <0.5%" if d < 0.5 else "0.5-1.5%" if d < 1.5 else ">1.5% stretched"
        buckets.setdefault(key, []).append(r["pnl"])
    for k in ("on magnet <0.5%", "0.5-1.5%", ">1.5% stretched"):
        p = buckets.get(k, [])
        if len(p) < 30:
            continue
        print(f"{k:>14}{len(p):>6}{sum(1 for x in p if x>0)/len(p)*100:>6.0f}%"
              f"{sum(p)/len(p):>9.2f}{min(p):>8.0f}")


def part_c_opex_pinning(df):
    print("\n== C. OPEX pinning ==")
    opex = {third_friday(y, m) for y in range(2018, 2027) for m in range(1, 13)}
    df2 = df[df.index.year >= 2018]
    wk_ranges, opex_flags = [], []
    # weekly high-low range as % of close, tagged OPEX week or not
    for _, wk in df2.groupby(pd.Grouper(freq="W-FRI")):
        if len(wk) < 3:
            continue
        rng = (wk["high"].max() - wk["low"].min()) / wk["close"].iloc[-1] * 100
        is_opex = any(d.date() in opex for d in wk.index)
        wk_ranges.append(rng)
        opex_flags.append(is_opex)
    s = pd.Series(wk_ranges)
    f = pd.Series(opex_flags)
    print(f"  weekly range: OPEX weeks {s[f].mean():.2f}% (n={f.sum()}) vs "
          f"ordinary {s[~f].mean():.2f}% (n={(~f).sum()})")

    print(f"\n  7DTE condor entered MONDAY, OPEX week vs other weeks (chop only):")
    print(f"{'entry':>14}{'n':>6}{'win%':>7}{'avg':>9}{'worst':>8}")
    groups = {"OPEX Monday": [], "other Monday": []}
    for i in range(len(df2) - 2):
        d = df2.index[i]
        if d.weekday() != 0 or d.year < 2018:
            continue
        if bool(df2["regime"].iloc[i]) or float(df2["adx"].iloc[i]) >= 32 \
           or float(df2["vix"].iloc[i]) >= 18:
            continue
        r = simulate(df2, i, "condor", 7)
        if r is None:
            continue
        wk_friday = d + pd.Timedelta(days=4 - d.weekday())
        key = "OPEX Monday" if wk_friday.date() in opex else "other Monday"
        groups[key].append(r["pnl"])
    for k, p in groups.items():
        if len(p) < 15:
            print(f"{k:>14}{len(p):>6}   (n<15 — no conclusions)")
            continue
        print(f"{k:>14}{len(p):>6}{sum(1 for x in p if x>0)/len(p)*100:>6.0f}%"
              f"{sum(p)/len(p):>9.2f}{min(p):>8.0f}")


if __name__ == "__main__":
    d = add_features(load())
    part_a_ma20_magnet(d)
    part_b_condor_vs_magnet_distance(d)
    part_c_opex_pinning(d)
