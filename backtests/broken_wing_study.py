"""backtests/broken_wing_study.py -- does a broken-wing butterfly earn a slot?

User ask (2026-07-18): we've never tested ratio spreads. A naked ratio spread
breaks the defined-risk rule the whole project is built on, so the defensible
cousin is the BROKEN-WING BUTTERFLY (BWB): a ratio-spread-like wide profit zone
with a hard, capped max loss because it stays fully long a far wing.

Structure tested — PUT broken-wing butterfly, bullish/neutral lean (fits our
only tradeable regimes; a call BWB would lean bearish, which we never trade):
    +1 put at K_hi  (near the money, narrow upper wing)
    -2 put at K_mid (short body, ~short_delta OTM)
    +1 put at K_lo  (far OTM, WIDE lower wing)
  upper_wing = K_hi - K_mid (narrow), lower_wing = K_mid - K_lo (wide).
  The wide lower wing cheapens the trade toward a credit; the extra distance is
  the "break". Payoff:
    - S >= K_hi at expiry:  keep the entry credit (no upside risk) — the lean.
    - S  = K_mid:           structural peak = (upper_wing - net_debit).
    - S <= K_lo:            capped max loss = (lower_wing - upper_wing + net_debit).
  So it profits when SPY is flat-to-up (theta + drift) and only loses on a hard
  drop THROUGH the body — defined the whole way down.

Method: identical honesty rules and machinery as DTE_LADDER / MAGNET studies.
  - SPY+VIX 2018-present, LIVE regime rules (add_features/load).
  - r=0 BS marks, sigma=VIX, delta-picked strikes (_strike_for_delta).
  - Same management: 70% of structural max profit OR time-exit at round(dte*21/45).
  - Benchmark = the plain 0.20-delta / $5 condor (build_legs from the ladder),
    run side-by-side so BWB is judged AGAINST what we already trade, not in a
    vacuum.
  - OOS era split 2018-22 vs 2023+ ; a rung PASSES only if BOTH eras positive.
  - run_haircut(): 10% worse entry premium everywhere (SKEW_STRESS).
  - Magnet dimension: BWB in chop bucketed by |spot-MA20| at entry, mirroring
    magnet_study Part B, to see if stretched entries matter for the BWB too.

Doc: docs/BROKEN_WING_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.directional_spread_study import add_features, load
from backtests.dte_ladder_study import build_legs as condor_build_legs
from learning.exit_manager import bs_price
from signals.condor_calc import _strike_for_delta

DTES = (7, 14, 21, 30, 45)
TARGET = 0.70
TIME_EXIT_FRAC = 21 / 45

BWB_UPPER_WING = 5.0     # narrow upper wing (K_hi - K_mid)
BWB_LOWER_WING = 10.0    # wide lower wing (K_mid - K_lo) — the "break"
BWB_SHORT_DELTA = 0.30   # the 2 short body puts, OTM below spot


def build_bwb_legs(spot, sigma, dte, hurt=0.0, upper_wing=BWB_UPPER_WING,
                   lower_wing=BWB_LOWER_WING, short_delta=BWB_SHORT_DELTA):
    """PUT broken-wing butterfly.
    -> (legs [(opt,k,signed_qty)], net_debit, max_profit, max_loss) or None.
    `hurt` shifts the entry premium against us (SKEW_STRESS): net_debit worsens
    by `hurt` * |net_debit| (collect less credit / pay more debit). Wings and
    the short-body delta are parametrized so the robustness sweep can vary them."""
    t = dte / 365.0
    k_mid = _strike_for_delta("put", spot, t, sigma, short_delta)
    k_hi = k_mid + upper_wing
    k_lo = k_mid - lower_wing
    if k_lo <= 0:
        return None
    net_debit = (bs_price("put", spot, k_hi, t, sigma)
                 - 2 * bs_price("put", spot, k_mid, t, sigma)
                 + bs_price("put", spot, k_lo, t, sigma))
    if hurt:
        net_debit = net_debit + hurt * abs(net_debit)
    max_profit = (upper_wing - net_debit) * 100
    max_loss = (lower_wing - upper_wing + net_debit) * 100
    if max_profit <= 2.0:                    # degenerate / no room
        return None
    legs = [("put", k_hi, +1), ("put", k_mid, -2), ("put", k_lo, +1)]
    return legs, net_debit, max_profit, max_loss


def simulate(df, i, builder, dte):
    """Generic single-entry sim (mirrors dte_ladder.simulate) for any builder
    returning (legs, net_debit, max_profit, max_loss)."""
    spot = float(df["close"].iloc[i])
    sigma = float(df["vix"].iloc[i]) / 100.0
    built = builder(spot, sigma, dte)
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


def _condor_builder(spot, sigma, dte):
    return condor_build_legs("condor", spot, sigma, dte)


def _regime_days(df):
    trending = [i for i in range(len(df) - 2)
                if bool(df["regime"].iloc[i]) and float(df["ext_pct"].iloc[i]) <= 9.0]
    choppy = [i for i in range(len(df) - 2)
              if (not bool(df["regime"].iloc[i]))
              and float(df["adx"].iloc[i]) < 32.0
              and float(df["vix"].iloc[i]) < 18.0]
    return trending, choppy


def _row(name, dte, rows):
    p = [r["pnl"] for r in rows]
    old = [r["pnl"] for r in rows if r["era"] == "old"]
    new = [r["pnl"] for r in rows if r["era"] == "new"]
    avg_o = sum(old) / len(old) if old else float("nan")
    avg_n = sum(new) / len(new) if new else float("nan")
    prem = sum(r["prem"] for r in rows) / len(rows)
    both_pos = bool(old and new and avg_o > 0 and avg_n > 0)
    verdict = "PASS" if both_pos else "fail-OOS"
    print(f"{name:>10}{dte:>5}{len(p):>6}{sum(1 for x in p if x > 0)/len(p)*100:>6.0f}%"
          f"{sum(p)/len(p):>9.2f}{sum(p):>10.0f}{min(p):>8.0f}{prem:>9.2f}  "
          f"{avg_o:>9.2f}{avg_n:>8.2f}  {verdict}")


def run(hurt=0.0):
    tag = "  (10% fill haircut)" if hurt else ""
    print(f"=== broken-wing butterfly vs condor — DTE ladder per regime{tag} ===")
    df = add_features(load())
    df = df[df.index.year >= 2018]
    trending, choppy = _regime_days(df)
    print(f"days: trending_up_calm(ext<=9)={len(trending)}  choppy_low_vol={len(choppy)}")

    def bwb_builder(spot, sigma, dte):
        return build_bwb_legs(spot, sigma, dte, hurt=hurt)

    def condor_hc(spot, sigma, dte):
        built = condor_build_legs("condor", spot, sigma, dte)
        if built is None or not hurt:
            return built
        legs, net_debit, mp, ml = built
        credit = -net_debit * (1 - hurt)          # collect 10% less
        return legs, -credit, credit * 100, (5.0 - credit) * 100

    for regime_name, days in (("choppy_low_vol", choppy),
                              ("trending_up_calm", trending)):
        print(f"\n---- {regime_name} ----")
        print(f"{'struct':>10}{'dte':>5}{'n':>6}{'win%':>7}{'avg':>9}{'total':>10}"
              f"{'worst':>8}{'entry$':>9}  {'18-22':>9}{'23+':>8}  verdict")
        for name, builder in (("condor", condor_hc), ("bwb", bwb_builder)):
            for dte in DTES:
                rows = []
                for i in days:
                    r = simulate(df, i, builder, dte)
                    if r is None:
                        continue
                    spot = float(df["close"].iloc[i]); sg = float(df["vix"].iloc[i]) / 100.0
                    built = builder(spot, sg, dte)
                    prem = -built[1] if built else 0.0   # +credit / -debit
                    rows.append({"pnl": r["pnl"],
                                 "era": "old" if df.index[i].year <= 2022 else "new",
                                 "prem": prem})
                if len(rows) < 30:
                    continue
                _row(name, dte, rows)


def run_haircut():
    run(hurt=0.10)


def run_magnet():
    """BWB in chop by |spot-MA20| at entry (mirrors magnet_study Part B)."""
    print("\n=== BWB 7DTE in chop, by |spot - MA20| at entry ===")
    df = add_features(load())
    df = df[df.index.year >= 2018]
    _, choppy = _regime_days(df)
    print(f"{'bucket':>16}{'n':>6}{'win%':>7}{'avg':>9}{'worst':>8}  {'18-22':>9}{'23+':>8}")
    buckets = {}
    for i in choppy:
        r = simulate(df, i, lambda s, sg, d: build_bwb_legs(s, sg, d), 7)
        if r is None:
            continue
        dist = abs(float(df["ma20_dist"].iloc[i]))
        key = "on magnet <0.5%" if dist < 0.5 else "0.5-1.5%" if dist < 1.5 else ">1.5% stretched"
        buckets.setdefault(key, []).append(
            {"pnl": r["pnl"], "era": "old" if df.index[i].year <= 2022 else "new"})
    for k in ("on magnet <0.5%", "0.5-1.5%", ">1.5% stretched"):
        rows = buckets.get(k, [])
        if len(rows) < 30:
            continue
        p = [r["pnl"] for r in rows]
        old = [r["pnl"] for r in rows if r["era"] == "old"]
        new = [r["pnl"] for r in rows if r["era"] == "new"]
        avg_o = sum(old) / len(old) if old else float("nan")
        avg_n = sum(new) / len(new) if new else float("nan")
        print(f"{k:>16}{len(p):>6}{sum(1 for x in p if x > 0)/len(p)*100:>6.0f}%"
              f"{sum(p)/len(p):>9.2f}{min(p):>8.0f}  {avg_o:>9.2f}{avg_n:>8.2f}")


def run_sweep():
    """Parameter-robustness sweep: is the trend-regime 30/45DTE edge real, or a
    knob artifact? Vary short-body delta x (upper,lower) wing ratio, judge each
    cell under the 10% haircut with the OOS era split. A robust edge shows most
    cells PASS both eras; a knob artifact shows only the original (0.30, 5/10).
    Focused on trending_up_calm (where BWB showed promise) at 21/30/45 DTE."""
    print("=== BWB parameter-robustness sweep — trending_up_calm, 10% haircut ===")
    print("(PASS = both OOS eras positive; benchmark condor 45DTE ~ +$9/trade here)\n")
    df = add_features(load())
    df = df[df.index.year >= 2018]
    trending, _ = _regime_days(df)

    deltas = (0.25, 0.30, 0.35, 0.40)
    wings = ((3.0, 8.0), (5.0, 10.0), (5.0, 15.0), (3.0, 10.0))
    sweep_dtes = (21, 30, 45)

    print(f"{'delta':>6}{'wings':>9}{'dte':>5}{'n':>6}{'win%':>7}{'avg':>9}"
          f"{'worst':>8}{'entry$':>9}  {'18-22':>9}{'23+':>8}  verdict")
    summary = {dte: {"pass": 0, "total": 0} for dte in sweep_dtes}
    for sd in deltas:
        for (uw, lw) in wings:
            for dte in sweep_dtes:
                def builder(spot, sigma, d, _sd=sd, _uw=uw, _lw=lw):
                    return build_bwb_legs(spot, sigma, d, hurt=0.10,
                                          upper_wing=_uw, lower_wing=_lw, short_delta=_sd)
                rows = []
                for i in trending:
                    r = simulate(df, i, builder, dte)
                    if r is None:
                        continue
                    spot = float(df["close"].iloc[i]); sg = float(df["vix"].iloc[i]) / 100.0
                    built = builder(spot, sg, dte)
                    prem = -built[1] if built else 0.0
                    rows.append({"pnl": r["pnl"],
                                 "era": "old" if df.index[i].year <= 2022 else "new",
                                 "prem": prem})
                if len(rows) < 30:
                    continue
                p = [r["pnl"] for r in rows]
                old = [r["pnl"] for r in rows if r["era"] == "old"]
                new = [r["pnl"] for r in rows if r["era"] == "new"]
                avg_o = sum(old) / len(old) if old else float("nan")
                avg_n = sum(new) / len(new) if new else float("nan")
                prem = sum(r["prem"] for r in rows) / len(rows)
                both_pos = bool(old and new and avg_o > 0 and avg_n > 0)
                summary[dte]["total"] += 1
                summary[dte]["pass"] += 1 if both_pos else 0
                verdict = "PASS" if both_pos else "fail-OOS"
                print(f"{sd:>6.2f}{f'{uw:.0f}/{lw:.0f}':>9}{dte:>5}{len(p):>6}"
                      f"{sum(1 for x in p if x > 0)/len(p)*100:>6.0f}%{sum(p)/len(p):>9.2f}"
                      f"{min(p):>8.0f}{prem:>9.2f}  {avg_o:>9.2f}{avg_n:>8.2f}  {verdict}")
        print()
    print("---- robustness summary (cells passing both eras, under haircut) ----")
    for dte in sweep_dtes:
        s = summary[dte]
        print(f"  {dte}DTE: {s['pass']}/{s['total']} parameter combos PASS")


if __name__ == "__main__":
    run()
    print("\n")
    run_haircut()
    run_magnet()
    print("\n")
    run_sweep()
