"""backtests/seven_dte_structure_study.py -- what structure actually fits 7DTE?

The 7DTE condor is the worst thing on the books: n=8 live, 62% win, -$475 net,
avg -$59, and the three worst losses in the journal (-$300/-$233/-$215) all
came from it during the August 2026 thrust. The instinct is to retire it. The
user's instruction is better: find the structure that FITS the window.

docs/CALM_CALIBRATION.md says why the current one doesn't. In a calm week the
0.20-delta short strike is touched 25.7% of the time. A 45DTE condor absorbs
that — weeks of theta left, only has to be right at exit. A 7DTE condor has no
recovery time: its whole life IS that week.

So the hypothesis under test is NOT "7DTE is bad", it is:

    a 7DTE condor fails because its shorts are too CLOSE, and moving them out
    trades credit for survival at a rate that is net positive.

Sweep the short delta and let the data pick. Same honesty machinery as every
other study here: OOS era-split, 10% credit haircut, and now commissions,
because a 4-leg round trip is real money on a $30 credit.
Doc: docs/SEVEN_DTE_STRUCTURE_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

import config
from backtests.directional_spread_study import add_features, load
import backtests.dte_ladder_study as ladder
from backtests.dte_ladder_study import bs_price, _strike_for_delta

DTE = 7
WING = 5.0
COMMISSION = config.COMMISSION_PER_CONTRACT_LEG * 4 * 2   # 4 legs, round trip


def build_condor_at_delta(spot, sigma, dte, delta, wing=WING):
    """Iron condor with shorts at `delta`, `wing`-wide longs."""
    t = dte / 365.0
    sc = _strike_for_delta("call", spot, t, sigma, delta)
    sp = _strike_for_delta("put", spot, t, sigma, delta)
    lc, lp = sc + wing, sp - wing
    credit = (bs_price("call", spot, sc, t, sigma) + bs_price("put", spot, sp, t, sigma)
              - bs_price("call", spot, lc, t, sigma) - bs_price("put", spot, lp, t, sigma))
    if credit <= 0.02:
        return None
    legs = [("call", sc, -1), ("call", lc, +1), ("put", sp, -1), ("put", lp, +1)]
    return legs, -credit, credit * 100, (wing - credit) * 100


def simulate(df, i, delta, wing, hurt, stop_at_touch, close_dte=0):
    """close_dte=0 holds to expiry; close_dte=3 mirrors what the LIVE book
    actually does (learning/seven_dte_forward.CLOSE_DTE) — every live 7DTE
    exited by that rule, none reached expiry."""
    spot = float(df["close"].iloc[i])
    sigma = float(df["vix"].iloc[i]) / 100.0
    built = build_condor_at_delta(spot, sigma, DTE, delta, wing)
    if built is None:
        return None
    legs, net_debit, max_profit, max_loss = built
    credit = -net_debit
    if hurt:
        credit *= (1 - hurt)
    sc = max(k for o, k, q in legs if o == "call" and q < 0)
    sp = min(k for o, k, q in legs if o == "put" and q < 0)

    # DTE is CALENDAR days, the index is TRADING days. Walking `DTE - close_dte`
    # ROWS forward overshoots by ~40% and can run past expiry, so resolve the
    # exit against real dates. (This bug inflated the first run of this study.)
    expiry = df.index[i] + pd.Timedelta(days=DTE)
    stop_at = expiry - pd.Timedelta(days=close_dte)
    exit_i = None
    for j in range(i + 1, len(df)):
        if df.index[j] > stop_at:
            break
        exit_i = j
        px = float(df["close"].iloc[j])
        if stop_at_touch and (px >= sc or px <= sp):
            # Close at the touch: the short is ~ATM, so the spread is worth
            # roughly half the wing. Deliberately pessimistic.
            return credit * 100 - (wing / 2) * 100 - COMMISSION
    if exit_i is None:
        return None

    final = float(df["close"].iloc[exit_i])
    close_dte = max(0, (expiry - df.index[exit_i]).days)
    if close_dte > 0:
        # Closing EARLY means buying the spread back at its mark, not settling
        # intrinsic — there is still time value in the shorts we must pay for.
        t_left = close_dte / 365.0
        sig = float(df["vix"].iloc[exit_i]) / 100.0
        cost = (bs_price("call", final, sc, t_left, sig)
                + bs_price("put", final, sp, t_left, sig)
                - bs_price("call", final, sc + wing, t_left, sig)
                - bs_price("put", final, sp - wing, t_left, sig))
        cost = min(max(cost, 0.0), wing)
        return (credit - cost) * 100 - COMMISSION

    if final >= sc:
        loss = min(wing, final - sc)
    elif final <= sp:
        loss = min(wing, sp - final)
    else:
        loss = 0.0
    return (credit - loss) * 100 - COMMISSION


def _row(name, rows):
    if len(rows) < 30:
        print(f"{name:>26}{len(rows):>6}   (n<30 — skip)")
        return
    p = [r["pnl"] for r in rows]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    ao = sum(old) / len(old) if old else float("nan")
    an = sum(new) / len(new) if new else float("nan")
    ok = bool(old and new and ao > 0 and an > 0)
    print(f"{name:>26}{len(p):>6}{sum(1 for x in p if x>0)/len(p)*100:>7.0f}%"
          f"{sum(p)/len(p):>9.2f}{min(p):>9.0f}{ao:>9.2f}{an:>8.2f}   "
          f"{'PASS' if ok else 'fail-OOS'}")


def run(hurt=0.10):
    df = add_features(load())
    df = df[df.index.year >= 2018]
    calm = [i for i in range(len(df) - DTE - 1)
            if float(df["adx"].iloc[i]) < 32.0 and float(df["vix"].iloc[i]) < 18.0]

    print("=" * 92)
    print(f"7DTE STRUCTURE SWEEP — condor in calm regime, {DTE}DTE, "
          f"{int(hurt*100)}% credit haircut + ${COMMISSION:.2f} commissions")
    print("=" * 92)
    print(f"{'variant':>26}{'n':>6}{'win':>7}{'avg':>9}{'worst':>9}"
          f"{'18-22':>9}{'23+':>8}   verdict")

    for delta in (0.20, 0.16, 0.12, 0.10, 0.08):
        rows = []
        for i in calm:
            p = simulate(df, i, delta, WING, hurt, stop_at_touch=False)
            if p is not None:
                rows.append({"pnl": p,
                             "era": "old" if df.index[i].year <= 2022 else "new"})
        _row(f"{delta:.2f}delta hold-to-expiry", rows)

    print()
    for delta in (0.20, 0.12, 0.10):
        rows = []
        for i in calm:
            p = simulate(df, i, delta, WING, hurt, stop_at_touch=True)
            if p is not None:
                rows.append({"pnl": p,
                             "era": "old" if df.index[i].year <= 2022 else "new"})
        _row(f"{delta:.2f}delta stop-on-touch", rows)

    print()
    # THE decisive comparison: the live book closes at 3 DTE, never at expiry.
    for cd in (0, 1, 2, 3, 4):
        rows = []
        for i in calm:
            p = simulate(df, i, 0.20, WING, hurt, False, close_dte=cd)
            if p is not None:
                rows.append({"pnl": p,
                             "era": "old" if df.index[i].year <= 2022 else "new"})
        label = "hold to expiry" if cd == 0 else f"close at {cd} DTE"
        _row(f"0.20delta {label}", rows)

    print()
    for wing in (2.0, 5.0, 10.0):
        rows = []
        for i in calm:
            p = simulate(df, i, 0.12, wing, hurt, stop_at_touch=False)
            if p is not None:
                rows.append({"pnl": p,
                             "era": "old" if df.index[i].year <= 2022 else "new"})
        _row(f"0.12delta ${wing:.0f} wings", rows)
    print("=" * 92)


if __name__ == "__main__":
    run()
