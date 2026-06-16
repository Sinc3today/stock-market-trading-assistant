"""backtests/event_straddle_study.py -- is there a DIRECTION-AGNOSTIC event edge?

Our prior event work measured DRIFT (net direction, ~0) and post-event realized
vol — both dead. This tests the thing we never tested: MAGNITUDE vs PRICE. For
each scheduled event, buy the pre-event ATM straddle (real historical option
prices) and compare the realized move to the straddle cost.

  realized move >  straddle cost  -> LONG-STRADDLE edge (the move beats the premium)
  realized move <  straddle cost  -> IV-CRUSH edge (sell the over-priced premium)
  realized move ~= straddle cost  -> efficient = dead even on magnitude

You don't need to know the direction — only whether the wave clears the premium.
Honest caveats: small sample (~2yr of events), and the efficient-market prior is
"move ~= premium". This is a FIRST-CUT screen, not a strategy. Research only.

Run: python -m backtests.event_straddle_study
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

# Vetted scheduled-event dates (high confidence). NFP is computed (first Friday)
# so it carries zero date-table risk; FOMC is the published meeting calendar.
# CPI is deliberately omitted from v1 (release days vary; would need a vetted
# table) — add later once dates are verified.
FOMC_DATES = [
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),  date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),  date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
]


def nearest_friday_on_or_after(d: date) -> date:
    return d + timedelta(days=(4 - d.weekday()) % 7)   # Mon=0..Fri=4


def nfp_dates(start: date, end: date) -> list[date]:
    """First Friday of each month in [start, end] (NFP release day)."""
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        f = nearest_friday_on_or_after(date(y, m, 1))
        if start <= f <= end:
            out.append(f)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def straddle_outcome(strike: float, cost: float, exit_spot: float) -> dict:
    """ATM straddle held to expiry: payoff = |exit - strike| - cost."""
    move = round(abs(exit_spot - strike), 2)
    pnl_long = round(move - cost, 2)
    return {"move": move, "cost": round(cost, 2),
            "pnl_long": pnl_long, "pnl_short": round(-pnl_long, 2),
            "long_win": move > cost}


def summarize(outcomes: list[dict]) -> dict:
    import statistics
    n = len(outcomes)
    if not n:
        return {"n": 0}
    lp = sum(o["pnl_long"] for o in outcomes)
    lw = sum(1 for o in outcomes if o["long_win"])
    median = statistics.median(sorted(o["pnl_long"] for o in outcomes))
    return {
        "n": n,
        "long_mean":     round(lp / n, 2),
        "long_median":   round(median, 2),
        "long_winrate":  round(lw / n * 100, 1),
        "short_mean":    round(-lp / n, 2),
        "short_winrate": round((n - lw) / n * 100, 1),
    }


# ~round-trip bid/ask cost on a 4-leg SPY straddle (entry + exit). A gross edge
# smaller than this isn't real after execution.
COST_FLOOR = 0.50


def verdict(s: dict) -> str:
    """Honest call: a small positive mean with a NEGATIVE median is outlier-
    driven, not an edge; and anything under the cost floor is a wash net."""
    mean, med = s["long_mean"], s["long_median"]
    if abs(mean) < COST_FLOOR or (mean > 0) != (med > 0):
        return ("EFFICIENT / not robust — no tradeable magnitude edge "
                f"(mean ${mean:+.2f} but median ${med:+.2f}, under ${COST_FLOOR} cost floor)")
    if mean >= COST_FLOOR:
        return "possible LONG-STRADDLE tilt — verify (small sample, outlier-sensitive)"
    return "possible IV-CRUSH tilt — verify (small sample)"


def main():
    from data.options_history import OptionsHistory
    spy = pd.read_csv(os.path.join(os.path.dirname(__file__), "spy_history_yf.csv"),
                      index_col=0, parse_dates=True).sort_index()
    oh = OptionsHistory()
    sidx = spy.index

    # build event list within the SPY/option window
    lo, hi = sidx.min().date(), sidx.max().date()
    events = [("FOMC", d) for d in FOMC_DATES if lo <= d <= hi]
    events += [("NFP", d) for d in nfp_dates(date(2024, 1, 1), hi)]
    events.sort(key=lambda e: e[1])

    def prev_trading_close(d):
        ts = sidx[sidx < pd.Timestamp(d)]
        return (ts[-1], float(spy.loc[ts[-1], "close"])) if len(ts) else (None, None)

    def close_on_or_after(d):
        ts = sidx[sidx >= pd.Timestamp(d)]
        return (ts[0], float(spy.loc[ts[0], "close"])) if len(ts) else (None, None)

    rows, outcomes, skipped = [], [], 0
    for kind, ev in events:
        entry_ts, spot = prev_trading_close(ev)
        if spot is None:
            skipped += 1; continue
        K = round(spot)
        expiry = nearest_friday_on_or_after(ev)
        exit_ts, exit_spot = close_on_or_after(expiry)
        if exit_spot is None:
            skipped += 1; continue
        c = oh.leg_close("SPY", expiry, "C", K, entry_ts.date().isoformat())
        p = oh.leg_close("SPY", expiry, "P", K, entry_ts.date().isoformat())
        if not c or not p:
            skipped += 1; continue
        o = straddle_outcome(K, c + p, exit_spot)
        o.update(kind=kind, date=ev.isoformat(), spot=spot, K=K,
                 cost_pct=round((c + p) / spot * 100, 2),
                 move_pct=round(o["move"] / spot * 100, 2))
        rows.append(o); outcomes.append(o)

    print(f"Event-straddle first cut — SPY {lo}..{hi}")
    print(f"events priced: {len(outcomes)}  (skipped {skipped} — no option data)\n")
    print(f"  {'date':11} {'kind':5} {'cost%':>6} {'move%':>6} {'$long':>7} winner")
    for o in rows:
        winner = "LONG (move>cost)" if o["long_win"] else "SHORT (iv-crush)"
        print(f"  {o['date']:11} {o['kind']:5} {o['cost_pct']:>5.2f}% {o['move_pct']:>5.2f}% "
              f"{o['pnl_long']:>+7.2f} {winner}")
    s = summarize(outcomes)
    if s["n"]:
        print(f"\n  LONG straddle:   mean ${s['long_mean']:+.2f}  median ${s['long_median']:+.2f}/event  win {s['long_winrate']:.0f}%")
        print(f"  SHORT (iv-crush): mean ${s['short_mean']:+.2f}/event  win {s['short_winrate']:.0f}%")
        # conditional split — the one place a pattern might hide
        for kind in ("FOMC", "NFP"):
            sub = summarize([o for o in outcomes if o["kind"] == kind])
            if sub["n"]:
                print(f"    {kind:5} (n={sub['n']:>2}): long mean ${sub['long_mean']:+.2f} "
                      f"median ${sub['long_median']:+.2f} win {sub['long_winrate']:.0f}%")
        print(f"\n  VERDICT: {verdict(s)}")
        print("  NOTE: first-cut screen, small sample, gross of fees/slippage. Not a strategy.")


if __name__ == "__main__":
    main()
