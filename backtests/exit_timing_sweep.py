"""backtests/exit_timing_sweep.py -- is any other bucket running a SCALED rule?

The 7DTE condor gave up ~$28/trade to a time-stop derived by scaling the 45DTE
rule proportionally: round(7 * 21/45) = 3 (docs/SEVEN_DTE_STRUCTURE_STUDY.md).
Theta is convex in DTE, so proportional scaling is invalid — the last days hold
most of the decay.

The same `TIME_EXIT_FRAC = 21/45` still governs the broken-wing forward test,
which means BWB-30DTE closes at round(30 * 21/45) = 14. There are 17 open BWB
positions riding that number right now, so this is not academic.

This sweeps close-DTE for every live (structure, DTE) pair and reports where
the configured rule sits against the best available one. Same honesty
machinery as the rest: calm-regime population, OOS era-split, 10% credit
haircut, commissions.

Doc: docs/EXIT_TIMING_SWEEP.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

import config
from backtests.directional_spread_study import add_features, load
from backtests.dte_ladder_study import bs_price, _strike_for_delta

HAIRCUT = 0.10
TARGET_PCT = 0.70          # live parity: close at 70% of max profit
BWB_SHORT_DELTA = 0.35
BWB_UPPER = 3.0
BWB_LOWER = 8.0
CONDOR_DELTA = 0.20
CONDOR_WING = 5.0


def _fee(n_legs):
    return config.COMMISSION_PER_CONTRACT_LEG * n_legs * 2


# ── structures ───────────────────────────────────────────────────

def condor_open(spot, sigma, dte):
    t = dte / 365.0
    sc = _strike_for_delta("call", spot, t, sigma, CONDOR_DELTA)
    sp = _strike_for_delta("put", spot, t, sigma, CONDOR_DELTA)
    lc, lp = sc + CONDOR_WING, sp - CONDOR_WING
    credit = (bs_price("call", spot, sc, t, sigma) + bs_price("put", spot, sp, t, sigma)
              - bs_price("call", spot, lc, t, sigma) - bs_price("put", spot, lp, t, sigma))
    if credit <= 0.02:
        return None
    return {"credit": credit, "k": (sc, lc, sp, lp), "legs": 4,
            "max_profit": credit * 100}


def condor_cost(st, spot, sigma, dte_left):
    sc, lc, sp, lp = st["k"]
    if dte_left <= 0:
        return (max(0.0, min(CONDOR_WING, spot - sc))
                + max(0.0, min(CONDOR_WING, sp - spot)))
    t = dte_left / 365.0
    return max(0.0, min(CONDOR_WING,
               bs_price("call", spot, sc, t, sigma) + bs_price("put", spot, sp, t, sigma)
               - bs_price("call", spot, lc, t, sigma) - bs_price("put", spot, lp, t, sigma)))


def bwb_open(spot, sigma, dte):
    t = max(dte, 1) / 365.0
    k_mid = _strike_for_delta("put", spot, t, sigma, BWB_SHORT_DELTA)
    k_hi, k_lo = k_mid + BWB_UPPER, k_mid - BWB_LOWER
    if k_lo <= 0:
        return None

    def px(k):
        return bs_price("put", spot, k, t, sigma)
    net_debit = px(k_hi) - 2 * px(k_mid) + px(k_lo)
    credit = -net_debit
    if (BWB_UPPER - net_debit) <= 0.02:
        return None
    return {"credit": credit, "k": (k_hi, k_mid, k_lo), "legs": 4,
            "max_profit": (BWB_UPPER + credit) * 100}


def bwb_cost(st, spot, sigma, dte_left):
    """Cost to CLOSE. A BWB is long a far wing, so this can be negative — you
    can be paid to close. Deliberately NOT clamped (audit A2)."""
    k_hi, k_mid, k_lo = st["k"]
    if dte_left <= 0:
        v = (max(0.0, k_hi - spot) - 2 * max(0.0, k_mid - spot)
             + max(0.0, k_lo - spot))
    else:
        t = dte_left / 365.0
        v = (bs_price("put", spot, k_hi, t, sigma)
             - 2 * bs_price("put", spot, k_mid, t, sigma)
             + bs_price("put", spot, k_lo, t, sigma))
    return -v


STRUCTURES = {
    "condor": (condor_open, condor_cost),
    "bwb": (bwb_open, bwb_cost),
}


def simulate(df, i, structure, dte, close_dte):
    """DTE is CALENDAR days; the index is TRADING days. Walking `dte - close_dte`
    ROWS forward silently overshoots by ~40% (7 rows = ~10 calendar days), which
    on a 7DTE trade means holding past expiry. Resolve the exit against real
    dates instead.
    """
    opener, coster = STRUCTURES[structure]
    spot = float(df["close"].iloc[i])
    sigma = float(df["vix"].iloc[i]) / 100.0
    st = opener(spot, sigma, dte)
    if st is None:
        return None
    credit = st["credit"] * (1 - HAIRCUT) if st["credit"] > 0 else st["credit"]

    expiry = df.index[i] + pd.Timedelta(days=dte)
    stop_at = expiry - pd.Timedelta(days=close_dte)
    max_profit = st["max_profit"]

    j = None
    for k in range(i + 1, len(df)):
        if df.index[k] > stop_at:
            break
        j = k
        days_left = max(0, (expiry - df.index[k]).days)
        spot_k = float(df["close"].iloc[k])
        sig_k = float(df["vix"].iloc[k]) / 100.0
        pnl = (credit - coster(st, spot_k, sig_k, days_left)) * 100
        # The live rule is "70% of max profit OR the time stop", and the target
        # fires first on most trades. Testing the time stop alone misrepresents
        # every bucket — the stop is a backstop, not the primary exit.
        if max_profit > 0 and pnl >= TARGET_PCT * max_profit:
            return pnl - _fee(st["legs"])
    if j is None:
        return None
    days_left = max(0, (expiry - df.index[j]).days)
    cost = coster(st, float(df["close"].iloc[j]),
                  float(df["vix"].iloc[j]) / 100.0, days_left)
    return (credit - cost) * 100 - _fee(st["legs"])


def _stats(rows):
    if len(rows) < 30:
        return None
    p = [r["pnl"] for r in rows]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    ao = sum(old) / len(old) if old else float("nan")
    an = sum(new) / len(new) if new else float("nan")
    return {"n": len(p), "win": sum(1 for x in p if x > 0) / len(p) * 100,
            "avg": sum(p) / len(p), "old": ao, "new": an,
            "pass": bool(old and new and ao > 0 and an > 0)}


def sweep(df, days, structure, dte, configured, candidates):
    print(f"\n  {structure.upper()} {dte}DTE   (configured: close at {configured} DTE)")
    print(f"    {'close at':>10}{'n':>6}{'win':>7}{'avg':>10}{'18-22':>9}{'23+':>8}   verdict")
    best, best_avg = None, float("-inf")
    for cd in candidates:
        rows = []
        for i in days:
            if i + dte >= len(df):
                continue
            p = simulate(df, i, structure, dte, cd)
            if p is not None:
                rows.append({"pnl": p,
                             "era": "old" if df.index[i].year <= 2022 else "new"})
        s = _stats(rows)
        if not s:
            continue
        flag = "  <-- CONFIGURED" if cd == configured else ""
        print(f"    {cd:>10}{s['n']:>6}{s['win']:>6.0f}%{s['avg']:>10.2f}"
              f"{s['old']:>9.2f}{s['new']:>8.2f}   "
              f"{'PASS' if s['pass'] else 'fail-OOS'}{flag}")
        if s["pass"] and s["avg"] > best_avg:
            best, best_avg = cd, s["avg"]
        if cd == configured:
            cfg = s
    if best is not None and cfg:
        gap = best_avg - cfg["avg"]
        verdict = ("OK — configured is the best passing rule" if best == configured
                   else f"LEAK — close at {best} DTE is +${gap:.2f}/trade better")
        print(f"    => {verdict}")


def run():
    df = add_features(load())
    df = df[df.index.year >= 2018]
    calm = [i for i in range(len(df))
            if float(df["adx"].iloc[i]) < 32.0 and float(df["vix"].iloc[i]) < 18.0]

    print("=" * 88)
    print("EXIT-TIMING SWEEP — is any bucket running a proportionally-scaled rule?")
    print(f"  calm regime, 2018+, {int(HAIRCUT*100)}% credit haircut + commissions")
    print("=" * 88)

    # 45DTE condor: 21 is the NATIVE, backtested rule — the control.
    sweep(df, calm, "condor", 45, 21, [7, 14, 21, 28, 35])
    # 7DTE condor: fixed 2026-09-06, shown for completeness.
    sweep(df, calm, "condor", 7, 1, [0, 1, 2, 3, 4])
    # BWB 45DTE: TIME_EXIT_FRAC gives 21 — native-equivalent.
    sweep(df, calm, "bwb", 45, 21, [7, 14, 21, 28, 35])
    # BWB 30DTE: TIME_EXIT_FRAC gives round(30*21/45) = 14 — SCALED, suspect.
    sweep(df, calm, "bwb", 30, 14, [3, 7, 10, 14, 18, 21])
    print("\n" + "=" * 88)


if __name__ == "__main__":
    run()
