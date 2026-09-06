"""backtests/dte_ladder_exit_study.py -- the right exit for every rung.

The exit-timing sweep (docs/EXIT_TIMING_SWEEP.md) found the 45DTE condor's
configured 21-DTE close testing marginal-to-negative after commissions, with
close-at-7 far better. I refused to act on that, because mean P&L alone cannot
answer it: holding a condor deep into expiry week is a GAMMA bet, and the case
for closing at 21 DTE has never been about the average — it is about the tail.

So this study asks the question properly, for every rung we run or might run
(45 / 30 / 21 / 14 / 7 DTE), reporting what actually decides it:

  * mean AND median (a mean carried by a few big winners is not an edge)
  * worst loss and 5th-percentile loss (the tail the average hides)
  * avg win / avg loss ratio (the condor's whole thesis is asymmetry)
  * mean/std — return per unit of variability
  * max adverse excursion during the hold (path pain, not just the endpoint)
  * OOS era split, 10% credit haircut, real commissions

Cross-checked against exit_timing_sweep: 7DTE close-at-1 and 45DTE close-at-21
must reproduce. When a study contradicts an established one, suspect the study
(that is how the calendar bug was caught).

Doc: docs/DTE_LADDER_EXIT_STUDY.md
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

import config
from backtests.directional_spread_study import add_features, load
from backtests.dte_ladder_study import bs_price, _strike_for_delta
from backtests.exit_timing_sweep import condor_open, condor_cost, TARGET_PCT, HAIRCUT

FEE = config.COMMISSION_PER_CONTRACT_LEG * 4 * 2      # 4-leg round trip

# Rungs to test, and the close-DTE candidates that make sense for each.
LADDER = {
    45: [7, 10, 14, 18, 21, 28],
    30: [3, 5, 7, 10, 14, 18],
    21: [2, 3, 5, 7, 10, 14],
    14: [1, 2, 3, 5, 7, 10],
    7:  [0, 1, 2, 3, 4],
}
CONFIGURED = {45: 21, 30: None, 21: None, 14: None, 7: 1}


def simulate(df, i, dte, close_dte):
    """Returns (pnl, max_adverse_excursion) or None.

    MAE is the worst mark-to-market seen during the hold — the path pain a
    close-only endpoint number cannot show.
    """
    spot = float(df["close"].iloc[i])
    sigma = float(df["vix"].iloc[i]) / 100.0
    st = condor_open(spot, sigma, dte)
    if st is None:
        return None
    credit = st["credit"] * (1 - HAIRCUT)
    max_profit = st["max_profit"]

    expiry = df.index[i] + pd.Timedelta(days=dte)
    stop_at = expiry - pd.Timedelta(days=close_dte)
    j, mae, reached = None, 0.0, False
    for k in range(i + 1, len(df)):
        if df.index[k] > stop_at:
            reached = True          # we actually got to the exit date
            break
        j = k
        days_left = max(0, (expiry - df.index[k]).days)
        pnl = (credit - condor_cost(st, float(df["close"].iloc[k]),
                                    float(df["vix"].iloc[k]) / 100.0,
                                    days_left)) * 100
        mae = min(mae, pnl)
        if max_profit > 0 and pnl >= TARGET_PCT * max_profit:
            return pnl - FEE, mae
    # Falling off the end of the data is NOT an exit. Returning a P&L for a
    # hold that never completed silently truncates every trade in the last
    # `dte` days of the sample and reports it as if it finished.
    if j is None or not reached:
        return None
    days_left = max(0, (expiry - df.index[j]).days)
    cost = condor_cost(st, float(df["close"].iloc[j]),
                       float(df["vix"].iloc[j]) / 100.0, days_left)
    return (credit - cost) * 100 - FEE, mae


def stats(rows):
    if len(rows) < 30:
        return None
    p = [r["pnl"] for r in rows]
    wins = [x for x in p if x > 0]
    losses = [x for x in p if x <= 0]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    ao = sum(old) / len(old) if old else float("nan")
    an = sum(new) / len(new) if new else float("nan")
    sp = sorted(p)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    sd = statistics.pstdev(p) if len(p) > 1 else 0.0
    return {
        "n": len(p),
        "win": len(wins) / len(p) * 100,
        "avg": sum(p) / len(p),
        "median": statistics.median(p),
        "worst": min(p),
        "p05": sp[max(0, int(len(sp) * 0.05) - 1)],
        "avg_win": aw,
        "avg_loss": al,
        "ratio": (aw / abs(al)) if al else float("inf"),
        "sharpe": (sum(p) / len(p) / sd) if sd else 0.0,
        "mae": sum(r["mae"] for r in rows) / len(rows),
        "old": ao, "new": an,
        "pass": bool(old and new and ao > 0 and an > 0),
    }


def run():
    df = add_features(load())
    df = df[df.index.year >= 2018]
    calm = [i for i in range(len(df))
            if float(df["adx"].iloc[i]) < 32.0 and float(df["vix"].iloc[i]) < 18.0]

    print("=" * 108)
    print("DTE LADDER — the right exit for each rung")
    print(f"  0.20-delta condor, $5 wings, calm regime, 2018+, "
          f"{int(HAIRCUT*100)}% haircut + ${FEE:.2f} commissions, "
          f"live {int(TARGET_PCT*100)}%-target rule")
    print("  MAE = average worst mark seen DURING the hold (path pain)")
    print("=" * 108)

    best_by_rung = {}
    for dte in (45, 30, 21, 14, 7):
        print(f"\n  ── {dte}DTE " + "─" * 92)
        print(f"    {'close':>6}{'n':>6}{'win':>6}{'avg':>9}{'med':>8}"
              f"{'p05':>9}{'worst':>9}{'W/L':>7}{'mean/sd':>9}{'MAE':>9}"
              f"{'18-22':>9}{'23+':>8}  verdict")
        best, best_score = None, float("-inf")
        for cd in LADDER[dte]:
            rows = []
            for i in calm:
                r = simulate(df, i, dte, cd)
                if r is not None:
                    rows.append({"pnl": r[0], "mae": r[1],
                                 "era": "old" if df.index[i].year <= 2022 else "new"})
            s = stats(rows)
            if not s:
                continue
            mark = " <-- live" if CONFIGURED.get(dte) == cd else ""
            print(f"    {cd:>6}{s['n']:>6}{s['win']:>5.0f}%{s['avg']:>9.2f}"
                  f"{s['median']:>8.2f}{s['p05']:>9.0f}{s['worst']:>9.0f}"
                  f"{s['ratio']:>7.2f}{s['sharpe']:>9.3f}{s['mae']:>9.0f}"
                  f"{s['old']:>9.2f}{s['new']:>8.2f}  "
                  f"{'PASS' if s['pass'] else 'fail-OOS'}{mark}")
            # Rank on risk-adjusted return, not raw mean — the whole point.
            if s["pass"] and s["sharpe"] > best_score:
                best, best_score = cd, s["sharpe"]
        best_by_rung[dte] = best
        if best is not None:
            cfg = CONFIGURED.get(dte)
            note = ("configured rule is already the best risk-adjusted choice"
                    if cfg == best else
                    f"best risk-adjusted exit is {best} DTE"
                    + (f" (live runs {cfg})" if cfg is not None else ""))
            print(f"    => {note}")
        else:
            print("    => no exit setting survives the OOS split")

    print("\n" + "=" * 108)
    print("  Best risk-adjusted exit per rung:",
          ", ".join(f"{k}DTE->{v}" for k, v in best_by_rung.items()))
    print("=" * 108)
    return best_by_rung


if __name__ == "__main__":
    run()
