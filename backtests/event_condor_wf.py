"""backtests/event_condor_wf.py -- FOMC + CPI condors with a strike-placement sweep.

Two extensions of the FOMC-condor lead, per the user's ask:
  1. EXTEND TO CPI — does "sell defined-risk premium into the event" generalize
     beyond FOMC to the other big monthly vol event? (roughly doubles the sample)
  2. BREACH MITIGATION — the FOMC edge is killed by a ~14% near-max-breach tail.
     Sweep the short-strike placement (shorts at move_mult x the expected move):
     wider shorts win more often / collect less credit. Does pushing them out cut
     the breach tail enough to improve net expectancy?

Reuses the (tested) condor pricing from fomc_condor_wf; real Polygon option prices,
held to expiry, net of slippage. Research only — small samples, in-sample.
Run: python -m backtests.event_condor_wf
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.event_straddle_study import FOMC_DATES, CPI_DATES, nearest_friday_on_or_after
from backtests.fomc_condor_wf import (
    condor_strikes, condor_credit, condor_pnl, summarize,
    WING_WIDTH, SLIPPAGE_PER_LEG,
)

MOVE_MULTS = [1.0, 1.25, 1.5]   # short-strike placement (x expected move)
BREACH_THRESHOLD = -200.0       # a loss worse than this = a breach (near-max)


def build_events(lo, hi):
    evs = [("FOMC", d) for d in FOMC_DATES if lo <= d <= hi]
    evs += [("CPI", d) for d in CPI_DATES if lo <= d <= hi]
    evs.sort(key=lambda e: e[1])
    return evs


def loss_stats(pnls, breach_threshold: float = BREACH_THRESHOLD) -> dict:
    n = len(pnls)
    if not n:
        return {"breach_rate": 0.0, "worst": 0.0}
    return {"breach_rate": round(sum(1 for p in pnls if p < breach_threshold) / n * 100, 1),
            "worst": round(min(pnls), 2)}


def _price_condor(oh, spy, sidx, kind, ev, move_mult):
    """Return P&L for one event's condor at a given strike placement, or None."""
    ts_prev = sidx[sidx < pd.Timestamp(ev)]
    if not len(ts_prev):
        return None
    entry_ts = ts_prev[-1]; spot = float(spy.loc[entry_ts, "close"])
    expiry = nearest_friday_on_or_after(ev)
    ts_exit = sidx[sidx >= pd.Timestamp(expiry)]
    if not len(ts_exit):
        return None
    exit_spot = float(spy.loc[ts_exit[0], "close"])
    on, K = entry_ts.date().isoformat(), round(spot)
    atm_c = oh.leg_close("SPY", expiry, "C", K, on)
    atm_p = oh.leg_close("SPY", expiry, "P", K, on)
    if not atm_c or not atm_p:
        return None
    em = (atm_c + atm_p) * move_mult
    lp, sp, sc, lc = condor_strikes(spot, em)
    sp_c = oh.leg_close("SPY", expiry, "P", sp, on)
    lp_c = oh.leg_close("SPY", expiry, "P", lp, on)
    sc_c = oh.leg_close("SPY", expiry, "C", sc, on)
    lc_c = oh.leg_close("SPY", expiry, "C", lc, on)
    if None in (sp_c, lp_c, sc_c, lc_c):
        return None
    credit = condor_credit(sp_c, lp_c, sc_c, lc_c) - 4 * SLIPPAGE_PER_LEG
    return condor_pnl(credit, lp, sp, sc, lc, exit_spot)


def main():
    from data.options_history import OptionsHistory
    spy = pd.read_csv(os.path.join(os.path.dirname(__file__), "spy_history_yf.csv"),
                      index_col=0, parse_dates=True).sort_index()
    oh, sidx = OptionsHistory(), None
    spy = spy; sidx = spy.index
    lo, hi = sidx.min().date(), sidx.max().date()
    events = build_events(lo, hi)

    print(f"Event-condor WF — FOMC + CPI, ${WING_WIDTH} wings, net ${SLIPPAGE_PER_LEG}/leg")
    print(f"events in window: {sum(1 for k,_ in events if k=='FOMC')} FOMC + "
          f"{sum(1 for k,_ in events if k=='CPI')} CPI\n")
    print(f"  {'placement':10} {'group':10} {'n':>3} {'win%':>5} {'mean':>7} {'breach%':>7} {'worst':>8} {'total':>8}")

    for mult in MOVE_MULTS:
        priced = {}   # kind -> [pnls]
        for kind, ev in events:
            p = _price_condor(oh, spy, sidx, kind, ev, mult)
            if p is not None:
                priced.setdefault(kind, []).append(p)
        allp = [p for v in priced.values() for p in v]
        for group, pnls in [("FOMC", priced.get("FOMC", [])),
                            ("CPI", priced.get("CPI", [])),
                            ("ALL", allp)]:
            if not pnls:
                continue
            s, ls = summarize(pnls), loss_stats(pnls)
            print(f"  {f'{mult:.2f}x EM':10} {group:10} {s['n']:>3} {s['win_rate']:>4.0f}% "
                  f"{s['mean']:>+7.0f} {ls['breach_rate']:>6.0f}% {ls['worst']:>+8.0f} {s['total']:>+8.0f}")
        print()

    print("  Read: does CPI behave like FOMC (sell-premium works)? And does a wider")
    print("  placement (1.25-1.5x) cut breach% enough to lift mean vs 1.0x? Small N, in-sample.")


if __name__ == "__main__":
    main()
