"""backtests/calm_calibration_backfill.py -- was "calm" ever a true label?

Runs learning.calm_calibration over the historical record so the instrument
answers TODAY instead of accumulating for a fortnight. The live daily job then
keeps it current.

The question: on days the classifier calls CALM (ADX < 32, VIX < 18 — the
condor's home regime), does the next week stay inside the band VIX implied?
If realised routinely exceeds implied, "calm" is systematically underpricing
risk and the August blow-through was a symptom, not an accident.

Read-only. Writes nothing.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
from learning import calm_calibration as cc

ADX_TREND_MIN = 32.0
VIX_CALM_MAX = 18.0


def _rows(df, mask, label):
    out = []
    for i in range(len(df) - cc.HORIZON_DAYS):
        if not mask.iloc[i]:
            continue
        vix = float(df["vix"].iloc[i])
        spot = float(df["close"].iloc[i])
        fut = float(df["close"].iloc[i + cc.HORIZON_DAYS])
        move = (fut - spot) / spot * 100
        r = cc.score_day(vix, move)
        if r:
            r["date"] = df.index[i]
            r["vix"] = vix
            r["era"] = "old" if df.index[i].year <= 2022 else "new"
            out.append(r)
    return out


def _report(name, rows):
    s = cc.summarise(rows)
    if not s["n"]:
        print(f"{name:>26}  (no days)")
        return s
    print(f"{name:>26}{s['n']:>7}{s['breach_pct']:>10.1f}%"
          f"{s['median_ratio']:>11.2f}{s['mean_ratio']:>10.2f}   {s['verdict']}")
    return s


def run():
    df = add_features(load())
    df = df[df.index.year >= 2018]

    calm = (df["adx"] < ADX_TREND_MIN) & (df["vix"] < VIX_CALM_MAX)
    loud = ~calm

    print("=" * 84)
    print(f'IS "CALM" A TRUE LABEL?  realised vs VIX-implied over '
          f'{cc.HORIZON_DAYS} sessions')
    print(f'  breach = move beyond {cc.SHORT_STRIKE_SIGMAS} sigma '
          f'(where a 0.20-delta short strike sits)')
    print("=" * 84)
    print(f"{'bucket':>26}{'n':>7}{'breach':>11}{'med ratio':>11}{'mean':>10}   verdict")

    calm_rows = _rows(df, calm, "calm")
    _report("CALM (ADX<32, VIX<18)", calm_rows)
    _report("not calm", _rows(df, loud, "loud"))

    print()
    # Era split — the honesty check every study here gets.
    _report("calm · 2018-2022", [r for r in calm_rows if r["era"] == "old"])
    _report("calm · 2023+", [r for r in calm_rows if r["era"] == "new"])

    print()
    # Does a LOWER VIX mean a safer week, as the label assumes?
    for lo, hi in ((0, 13), (13, 15), (15, 16.5), (16.5, 18)):
        band = [r for r in calm_rows if lo <= r["vix"] < hi]
        _report(f"calm · VIX {lo}-{hi}", band)

    print()
    # The August case: calm label immediately after a fast move.
    df2 = df.copy()
    df2["prior5"] = (df2["close"] / df2["close"].shift(5) - 1).abs() * 100
    after_thrust = calm & (df2["prior5"] > 3.0)
    _report("calm AFTER a >3% week", _rows(df, after_thrust, "thrust"))
    quiet_entry = calm & (df2["prior5"] <= 1.5)
    _report("calm after a quiet week", _rows(df, quiet_entry, "quiet"))

    print("=" * 84)
    s = cc.summarise(calm_rows)
    print(f"  A median ratio near {cc.OVERPRICED_BELOW}-{cc.UNDERPRICED_ABOVE} is a "
          "well-priced label: realised usually lands inside implied,")
    print("  which is exactly WHY selling premium works. Above "
          f"{cc.UNDERPRICED_ABOVE} would mean 'calm' is a lie.")
    print(f"  Verdict on the calm label: {s['verdict'].upper()} "
          f"(median realised = {s['median_ratio']:.2f}x implied, "
          f"{s['breach_pct']:.1f}% of weeks breach the short strike)")
    print("=" * 84)
    return calm_rows


if __name__ == "__main__":
    run()
