"""backtests/dte_ladder_study.py -- which DTE rungs earn their slot, per regime?

User ask (2026-07-15): before opening the disciplined book to 7/14/21/30DTE
buckets (3 slots each), test what's actually profitable at each DTE in each
tradeable regime.

Method (same honesty rules as DIRECTIONAL_SPREAD_STUDY / SHORT_DTE_CONDOR_STUDY):
  - SPY + VIX 2018-present (yfinance), regimes reconstructed with the LIVE
    rules: ADX(14) >= 32 & VIX < 18 & > 200MA -> trending_up_calm (with the
    live <=9% extension gate applied to entries); ADX < 32 & VIX < 18 ->
    choppy_low_vol.
  - Structures at each DTE: iron condor (0.20-delta shorts, $5 wings),
    bull put credit (sell 0.40-delta, $5 wing), bull call debit
    (buy 0.55-delta / sell 0.30-delta). r=0 Black-Scholes marks, sigma = VIX.
  - Management scaled from the live 45DTE rule (70% profit target, close at
    21 DTE = ~47% of life): time-exit when remaining DTE <= round(dte * 0.467),
    floor 1. Target 70% everywhere.
  - Every qualifying day enters (signal-quality measurement, overlap allowed).
  - OOS: each cell also split 2018-22 vs 2023+; a rung only "passes" if both
    eras are positive.

Output: one table per regime. Doc: docs/DTE_LADDER_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
from learning.exit_manager import bs_price
from signals.condor_calc import _strike_for_delta

DTES = (7, 14, 21, 30, 45)
TARGET = 0.70
TIME_EXIT_FRAC = 21 / 45          # live parity: 45DTE closes at 21 DTE


def build_legs(structure, spot, sigma, dte):
    """-> (legs [(opt,k,signed_qty)], net_debit, max_profit, max_loss) or None."""
    t = dte / 365.0
    if structure == "condor":
        sc = _strike_for_delta("call", spot, t, sigma, 0.20)
        sp = _strike_for_delta("put", spot, t, sigma, 0.20)
        lc, lp = sc + 5.0, sp - 5.0
        credit = (bs_price("call", spot, sc, t, sigma) + bs_price("put", spot, sp, t, sigma)
                  - bs_price("call", spot, lc, t, sigma) - bs_price("put", spot, lp, t, sigma))
        if credit <= 0.02:
            return None
        legs = [("call", sc, -1), ("call", lc, +1), ("put", sp, -1), ("put", lp, +1)]
        return legs, -credit, credit * 100, (5.0 - credit) * 100
    if structure == "put_credit":
        sk = _strike_for_delta("put", spot, t, sigma, 0.40)
        lk = sk - 5.0
        credit = bs_price("put", spot, sk, t, sigma) - bs_price("put", spot, lk, t, sigma)
        if credit <= 0.02:
            return None
        return [("put", sk, -1), ("put", lk, +1)], -credit, credit * 100, (5.0 - credit) * 100
    if structure == "call_debit":
        lk = _strike_for_delta("call", spot, t, sigma, 0.55)
        sk = _strike_for_delta("call", spot, t, sigma, 0.30)
        debit = bs_price("call", spot, lk, t, sigma) - bs_price("call", spot, sk, t, sigma)
        width = sk - lk
        if debit <= 0.02 or width <= debit:
            return None
        return [("call", lk, +1), ("call", sk, -1)], debit, (width - debit) * 100, debit * 100
    raise ValueError(structure)


def simulate(df, i, structure, dte):
    spot = float(df["close"].iloc[i])
    sigma = float(df["vix"].iloc[i]) / 100.0
    built = build_legs(structure, spot, sigma, dte)
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
        s = float(df["close"].iloc[j])
        sg = float(df["vix"].iloc[j]) / 100.0
        val = sum(q * bs_price(opt, s, k, max(t, 1e-6), sg) for opt, k, q in legs)
        pnl = (val - net_debit) * 100
        if pnl >= TARGET * max_profit:
            return {"pnl": TARGET * max_profit, "reason": "target"}
        if days_left <= time_exit:
            return {"pnl": pnl, "reason": "time"}
    return {"pnl": pnl, "reason": "eod"}


def run():
    df = add_features(load())
    df = df[df.index.year >= 2018]
    trending = [i for i in range(len(df) - 2)
                if bool(df["regime"].iloc[i]) and float(df["ext_pct"].iloc[i]) <= 9.0]
    choppy = [i for i in range(len(df) - 2)
              if (not bool(df["regime"].iloc[i]))
              and float(df["adx"].iloc[i]) < 32.0
              and float(df["vix"].iloc[i]) < 18.0]
    print(f"days: trending_up_calm(ext<=9)={len(trending)}  choppy_low_vol={len(choppy)}")

    for regime_name, days in (("choppy_low_vol", choppy),
                              ("trending_up_calm", trending)):
        print(f"\n==== {regime_name} ====")
        print(f"{'structure':>12}{'dte':>5}{'n':>6}{'win%':>7}{'avg':>9}{'total':>10}"
              f"{'worst':>8}  {'18-22 avg':>10}{'23+ avg':>9}  verdict")
        for structure in ("condor", "put_credit", "call_debit"):
            for dte in DTES:
                rows = []
                for i in days:
                    r = simulate(df, i, structure, dte)
                    if r is not None:
                        rows.append({"pnl": r["pnl"],
                                     "era": "old" if df.index[i].year <= 2022 else "new"})
                if len(rows) < 30:
                    continue
                p = [r["pnl"] for r in rows]
                old = [r["pnl"] for r in rows if r["era"] == "old"]
                new = [r["pnl"] for r in rows if r["era"] == "new"]
                avg_o = sum(old) / len(old) if old else float("nan")
                avg_n = sum(new) / len(new) if new else float("nan")
                both_pos = (old and new and avg_o > 0 and avg_n > 0)
                verdict = "PASS" if both_pos else "fail-OOS"
                print(f"{structure:>12}{dte:>5}{len(p):>6}"
                      f"{sum(1 for x in p if x > 0)/len(p)*100:>6.0f}%"
                      f"{sum(p)/len(p):>9.2f}{sum(p):>10.0f}{min(p):>8.0f}  "
                      f"{avg_o:>10.2f}{avg_n:>9.2f}  {verdict}")


if __name__ == "__main__":
    run()


def run_haircut():
    """SKEW_STRESS pass: 10% worse fills everywhere (credits -10%, debits +10%).
    A rung only earns a slot if it survives this too."""
    global build_legs
    orig = build_legs

    def haircut_legs(structure, spot, sigma, dte):
        built = orig(structure, spot, sigma, dte)
        if built is None:
            return None
        legs, net_debit, max_profit, max_loss = built
        if net_debit < 0:                       # credit structure
            credit = -net_debit * 0.90
            width = 5.0
            return legs, -credit, credit * 100, (width - credit) * 100
        debit = net_debit * 1.10                # debit structure
        width = max_profit / 100 + net_debit
        if width <= debit:
            return None
        return legs, debit, (width - debit) * 100, debit * 100

    build_legs = haircut_legs
    try:
        run()
    finally:
        build_legs = orig
