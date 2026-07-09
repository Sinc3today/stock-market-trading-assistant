"""backtests/crash_replay.py -- what would the bot have done in Feb-Apr 2020?

Audit T2#10: the 5-yr window (2021-2026) contains no crash — no VIX-80 event,
no -30% month. Replay 2019-2020 (yfinance, outside our CSV) through the CURRENT
regime classifier + condor sim and answer:
  1. Did the gates go to cash before/during the crash, or keep selling condors?
  2. What did condors entered in the pre-crash calm actually lose?
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.structure_comparison import condor_legs, simulate, CONDOR_REGIMES
from signals.regime_detector import RegimeDetector


def load_2019_2020():
    import yfinance as yf
    spy = yf.Ticker("SPY").history(start="2018-01-01", end="2020-12-31", auto_adjust=True)
    spy.index = pd.to_datetime(spy.index).tz_localize(None)
    spy = spy[["Open", "High", "Low", "Close", "Volume"]]
    spy.columns = ["open", "high", "low", "close", "volume"]
    vix = yf.Ticker("^VIX").history(start="2018-01-01", end="2020-12-31", auto_adjust=True)
    vix.index = pd.to_datetime(vix.index).tz_localize(None)
    vix_at = {d.date(): float(c) for d, c in vix["Close"].items()}
    spy.index = [d.date() for d in spy.index]
    return spy.sort_index(), vix_at


def run():
    spy_df, vix_at = load_2019_2020()
    print(f"bars: {len(spy_df)} ({spy_df.index[0]} -> {spy_df.index[-1]})")
    detector = RegimeDetector()
    dates = sorted(spy_df.index)

    from collections import Counter
    regimes_by_month = {}
    entries = []
    for i, d in enumerate(dates):
        if i < 210 or i > len(dates) - 35:
            continue
        hist = spy_df.loc[dates[max(0, i - 250):i + 1]].copy()
        hist.index = pd.to_datetime(hist.index)
        try:
            r = detector.classify(spy_daily_df=hist, vix_current=vix_at.get(d, 16.0),
                                  ivr_current=30.0, today=d)
        except Exception:
            continue
        m = d.strftime("%Y-%m")
        regimes_by_month.setdefault(m, Counter())[r.regime.value] += 1
        if r.regime in CONDOR_REGIMES:
            entries.append(i)

    print("\nregime mix by month (2020 focus):")
    for m in sorted(regimes_by_month):
        if not m.startswith("2020"):
            continue
        c = regimes_by_month[m]
        top = ", ".join(f"{k}:{v}" for k, v in c.most_common(3))
        print(f"  {m}: {top}")

    print(f"\ncondor entry days total: {len(entries)}")
    rows = []
    for i in entries:
        spot = float(spy_df.loc[dates[i], "close"])
        res = simulate(condor_legs(spot, 0.020), spy_df, dates, i, vix_at)
        if res:
            res["entry_date"] = dates[i]
            rows.append(res)
    wins = sum(1 for r in rows if r["outcome"] == "win")
    tot = sum(r["pnl"] for r in rows)
    print(f"condor results 2019-2020: n={len(rows)} win {wins/max(1,len(rows))*100:.1f}% "
          f"total ${tot:,.0f} avg ${tot/max(1,len(rows)):.2f}")

    # the crash window specifically
    crash = [r for r in rows if pd.Timestamp("2020-01-15").date() <= r["entry_date"]
             <= pd.Timestamp("2020-03-31").date()]
    if crash:
        cw = sum(1 for r in crash if r["outcome"] == "win")
        ct = sum(r["pnl"] for r in crash)
        worst = min(r["pnl"] for r in crash)
        print(f"entries Jan15-Mar31 2020: n={len(crash)} win {cw/len(crash)*100:.0f}% "
              f"total ${ct:,.0f} worst ${worst:,.0f}")
    else:
        print("entries Jan15-Mar31 2020: NONE — gates stood aside")


if __name__ == "__main__":
    run()
