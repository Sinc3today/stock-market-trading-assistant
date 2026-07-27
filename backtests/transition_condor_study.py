"""backtests/transition_condor_study.py -- is there a conservative play in the
VIX 18-22 "transition" chop, or is sitting out correct?

User ask (2026-07-27): on a choppy day with VIX ~20 (18-22 transition band), the
bot says "reduced condor or sit out." Is there actually a conservative condor
structure with edge here — even a defensive one — or is the stand-down right?
Learn where the line between discipline and missed opportunity really sits.

Idea under test: elevated vol means bigger moves, so the standard 0.20-delta
condor may get run over — but a FURTHER-OTM condor (0.10-0.15 delta shorts) gives
more room and might retain edge. Test delta x DTE in the transition band, vs the
calm-band (VIX<18) baseline, with the usual OOS era split + 10% haircut.

Same machinery/honesty as the DTE-ladder / BWB / IVR studies.
Doc: docs/TRANSITION_CONDOR_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
from learning.exit_manager import bs_price
from signals.condor_calc import _strike_for_delta

DELTAS = (0.20, 0.15, 0.10)
DTES = (7, 14, 21, 45)
WING = 5.0
TARGET = 0.70
TIME_EXIT_FRAC = 21 / 45


def build_condor_delta(spot, sigma, dte, delta=0.20, wing=WING, hurt=0.0):
    t = dte / 365.0
    sc = _strike_for_delta("call", spot, t, sigma, delta)
    sp = _strike_for_delta("put", spot, t, sigma, delta)
    lc, lp = sc + wing, sp - wing
    credit = (bs_price("call", spot, sc, t, sigma) + bs_price("put", spot, sp, t, sigma)
              - bs_price("call", spot, lc, t, sigma) - bs_price("put", spot, lp, t, sigma))
    if hurt:
        credit *= (1 - hurt)
    if credit <= 0.02:
        return None
    legs = [("call", sc, -1), ("call", lc, +1), ("put", sp, -1), ("put", lp, +1)]
    return legs, -credit, credit * 100, (wing - credit) * 100


def simulate(df, i, dte, delta, hurt=0.0):
    spot = float(df["close"].iloc[i]); sigma = float(df["vix"].iloc[i]) / 100.0
    built = build_condor_delta(spot, sigma, dte, delta, hurt=hurt)
    if built is None:
        return None
    legs, net_debit, max_profit, _ = built
    idx = df.index
    exp_date = idx[i] + pd.Timedelta(days=dte)
    time_exit = max(1, round(dte * TIME_EXIT_FRAC))
    pnl = 0.0
    for j in range(i + 1, len(idx)):
        days_left = (exp_date - idx[j]).days
        if days_left < 0:
            break
        t = days_left / 365.0
        s = float(df["close"].iloc[j]); sg = float(df["vix"].iloc[j]) / 100.0
        val = sum(q * bs_price(o, s, k, max(t, 1e-6), sg) for o, k, q in legs)
        pnl = (val - net_debit) * 100
        if pnl >= TARGET * max_profit:
            return {"pnl": TARGET * max_profit}
        if days_left <= time_exit:
            return {"pnl": pnl}
    return {"pnl": pnl}


def _row(name, dte, rows):
    if len(rows) < 30:
        print(f"{name:>10}{dte:>5}{len(rows):>6}   (n<30 — skip)")
        return
    p = [r["pnl"] for r in rows]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    ao = sum(old) / len(old) if old else float("nan")
    an = sum(new) / len(new) if new else float("nan")
    both = bool(old and new and ao > 0 and an > 0)
    print(f"{name:>10}{dte:>5}{len(p):>6}{sum(1 for x in p if x>0)/len(p)*100:>6.0f}%"
          f"{sum(p)/len(p):>9.2f}{min(p):>8.0f}  {ao:>9.2f}{an:>8.2f}  "
          f"{'PASS' if both else 'fail-OOS'}")


def _days(df, vlo, vhi):
    return [i for i in range(len(df) - 2)
            if (not bool(df["regime"].iloc[i]))
            and float(df["adx"].iloc[i]) < 32.0
            and vlo <= float(df["vix"].iloc[i]) < vhi]


def run(hurt=0.0):
    tag = "  (10% fill haircut)" if hurt else ""
    print(f"=== condor in VIX 18-22 TRANSITION chop, by short-delta x DTE{tag} ===")
    df = add_features(load()); df = df[df.index.year >= 2018]
    trans = _days(df, 18.0, 22.0)
    calm = _days(df, 0.0, 18.0)
    print(f"transition days (VIX 18-22, choppy): {len(trans)}  |  calm (VIX<18): {len(calm)}")
    print(f"{'delta':>10}{'dte':>5}{'n':>6}{'win%':>7}{'avg':>9}{'worst':>8}"
          f"  {'18-22':>9}{'23+':>8}  verdict")
    for delta in DELTAS:
        for dte in DTES:
            rows = []
            for i in trans:
                r = simulate(df, i, dte, delta, hurt=hurt)
                if r is not None:
                    rows.append({"pnl": r["pnl"],
                                 "era": "old" if df.index[i].year <= 2022 else "new"})
            _row(f"{delta:.2f}d", dte, rows)
        print()
    # calm-band baseline for reference (standard 0.20/45DTE)
    base = [{"pnl": simulate(df, i, 45, 0.20, hurt=hurt)["pnl"],
             "era": "old" if df.index[i].year <= 2022 else "new"}
            for i in calm if simulate(df, i, 45, 0.20, hurt=hurt) is not None]
    print("  reference — calm-band (VIX<18) 0.20d/45DTE condor:")
    _row("calm 0.20", 45, base)


if __name__ == "__main__":
    run()
    print()
    run(hurt=0.10)
