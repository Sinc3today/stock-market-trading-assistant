"""backtests/fomc_condor_wf.py -- does selling a defined-risk iron condor INTO
FOMC beat skipping it?

Follows the event_straddle lead: FOMC straddles are over-priced (the realized
move clears them only ~27% of the time). This prices a real iron condor sold the
day before each FOMC, shorts placed at the expected-move (straddle) breakevens
with fixed wings, held to expiry — using real Polygon option prices for the
entry credit and the SPY close for the expiry intrinsic. Net of a slippage
haircut. Baseline = skip (the bot's current behavior) = $0.

Honest: ~15 FOMCs is a SMALL sample — the IS/OOS split is directional, not
robust; lead with full-sample win-rate/expectancy + per-event consistency.
Research only. Run: python -m backtests.fomc_condor_wf
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.event_straddle_study import (
    FOMC_DATES, nearest_friday_on_or_after,
)

WING_WIDTH       = 5      # $ wing width each side
SLIPPAGE_PER_LEG = 0.05   # $/share haircut, 4 legs at entry
CONTRACT_MULT    = 100
OOS_FRACTION     = 0.60   # first 60% of FOMCs = in-sample


def condor_strikes(spot: float, expected_move: float, width: int = WING_WIDTH):
    """Short strikes at the expected-move (straddle) breakevens; wings `width` out."""
    short_put  = round(spot - expected_move)
    short_call = round(spot + expected_move)
    return short_put - width, short_put, short_call, short_call + width


def condor_credit(sp_close: float, lp_close: float,
                  sc_close: float, lc_close: float) -> float:
    """Net credit/share: sell the short put+call, buy the long wings."""
    return round((sp_close + sc_close) - (lp_close + lc_close), 4)


def condor_expiry_liability(long_put, short_put, short_call, long_call,
                            exit_spot: float) -> float:
    """Per-share intrinsic owed at expiry (put spread + call spread, each capped
    at its width)."""
    put_spread  = max(0.0, min(short_put - exit_spot, short_put - long_put))
    call_spread = max(0.0, min(exit_spot - short_call, long_call - short_call))
    return round(put_spread + call_spread, 4)


def condor_pnl(credit: float, long_put, short_put, short_call, long_call,
               exit_spot: float, mult: int = CONTRACT_MULT) -> float:
    """Dollar P&L at expiry for 1 contract: (credit - liability) * 100."""
    liab = condor_expiry_liability(long_put, short_put, short_call, long_call, exit_spot)
    return round((credit - liab) * mult, 2)


def summarize(pnls: list[float]) -> dict:
    n = len(pnls)
    if not n:
        return {"n": 0}
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "mean":     round(sum(pnls) / n, 2),
        "total":    round(sum(pnls), 2),
    }


def main():
    from data.options_history import OptionsHistory
    spy = pd.read_csv(os.path.join(os.path.dirname(__file__), "spy_history_yf.csv"),
                      index_col=0, parse_dates=True).sort_index()
    oh = OptionsHistory()
    sidx = spy.index
    lo, hi = sidx.min().date(), sidx.max().date()

    def prev_close(d):
        ts = sidx[sidx < pd.Timestamp(d)]
        return (ts[-1], float(spy.loc[ts[-1], "close"])) if len(ts) else (None, None)

    def close_on_or_after(d):
        ts = sidx[sidx >= pd.Timestamp(d)]
        return float(spy.loc[ts[0], "close"]) if len(ts) else None

    rows = []
    for ev in [d for d in FOMC_DATES if lo <= d <= hi]:
        entry_ts, spot = prev_close(ev)
        if spot is None:
            continue
        expiry = nearest_friday_on_or_after(ev)
        exit_spot = close_on_or_after(expiry)
        if exit_spot is None:
            continue
        on = entry_ts.date().isoformat()
        K = round(spot)
        # expected move = the ATM straddle price (the over-priced thing we're selling)
        atm_c = oh.leg_close("SPY", expiry, "C", K, on)
        atm_p = oh.leg_close("SPY", expiry, "P", K, on)
        if not atm_c or not atm_p:
            continue
        em = atm_c + atm_p
        lp, sp, sc, lc = condor_strikes(spot, em)
        # real prices for the 4 condor legs
        sp_c = oh.leg_close("SPY", expiry, "P", sp, on)
        lp_c = oh.leg_close("SPY", expiry, "P", lp, on)
        sc_c = oh.leg_close("SPY", expiry, "C", sc, on)
        lc_c = oh.leg_close("SPY", expiry, "C", lc, on)
        if None in (sp_c, lp_c, sc_c, lc_c):
            continue
        credit = condor_credit(sp_c, lp_c, sc_c, lc_c)
        credit_net = credit - 4 * SLIPPAGE_PER_LEG          # entry slippage haircut
        pnl = condor_pnl(credit_net, lp, sp, sc, lc, exit_spot)
        rows.append({"date": ev.isoformat(), "spot": spot, "shorts": f"{sp}/{sc}",
                     "credit": round(credit_net, 2), "exit": round(exit_spot, 2),
                     "pnl": pnl})

    print(f"FOMC iron-condor WF — shorts at expected-move breakevens, ${WING_WIDTH} wings, "
          f"net of ${SLIPPAGE_PER_LEG}/leg")
    print(f"FOMCs priced: {len(rows)}\n")
    print(f"  {'date':11} {'spot':>7} {'shorts':>11} {'credit':>6} {'exit':>7} {'pnl':>8}")
    for r in rows:
        print(f"  {r['date']:11} {r['spot']:>7.2f} {r['shorts']:>11} "
              f"{r['credit']:>6.2f} {r['exit']:>7.2f} {r['pnl']:>+8.2f}")
    pnls = [r["pnl"] for r in rows]
    cut = int(len(pnls) * OOS_FRACTION)
    full, ins, oos = summarize(pnls), summarize(pnls[:cut]), summarize(pnls[cut:])
    print(f"\n  FULL (n={full['n']}): win {full['win_rate']:.0f}%  mean ${full['mean']:+.0f}  total ${full['total']:+.0f}")
    print(f"  IS   (n={ins['n']}): win {ins.get('win_rate',0):.0f}%  mean ${ins.get('mean',0):+.0f}")
    print(f"  OOS  (n={oos['n']}): win {oos.get('win_rate',0):.0f}%  mean ${oos.get('mean',0):+.0f}")
    base = full["mean"]
    print(f"\n  VERDICT vs skip ($0): condor mean ${base:+.0f}/FOMC — "
          + ("BEATS skip" if base > 0 else "does NOT beat skip"))
    print(f"  NOTE: n={full['n']} is small — IS/OOS is directional only. In-sample-ish, "
          "one strike-placement, fixed wings. A lead, not a deployable strategy.")


if __name__ == "__main__":
    main()
