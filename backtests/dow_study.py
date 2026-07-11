"""backtests/dow_study.py -- day-of-week effects: is the user's Friday hunch real?

User observation (2026-07-10): Fridays feel consistently positive; weekends
sometimes reset the gains. Test it (5yr SPY):

  A. Raw calendar stats — per weekday: close->close return, intraday
     (open->close), overnight (prior close->open); plus the weekend specifically
     (Fri close -> Mon open gap, Fri close -> Mon close).
  B. Strategy overlay — the validated 2-day condor and the trending-day bull
     put spread, grouped by ENTRY weekday (pessimistic variant), to see whether
     entry timing is a real lever for OUR trades rather than a market trivia fact.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.structure_comparison import _classify_entries
from backtests.short_dte_condor_study import simulate as condor_sim, build_legs as _bl
from backtests.short_dte_condor_study import net_credit  # noqa: F401 (machinery import)

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def calendar_stats(spy_df):
    df = spy_df.copy()
    idx = list(df.index)
    rows = []
    for i in range(1, len(idx)):
        d = idx[i]
        prev = idx[i - 1]
        o, c = float(df.loc[d, "open"]), float(df.loc[d, "close"])
        pc = float(df.loc[prev, "close"])
        rows.append({"dow": pd.Timestamp(d).weekday(),
                     "cc": (c - pc) / pc * 100,          # close->close
                     "oc": (c - o) / o * 100,            # intraday
                     "co": (o - pc) / pc * 100,          # overnight gap into the day
                     "gap_days": (d - prev).days})
    r = pd.DataFrame(rows)
    print("== A1. per-weekday returns (5yr) ==")
    print(f"{'day':>4}{'n':>6}{'close→close':>14}{'win%':>7}{'intraday':>11}{'overnight':>11}")
    for w in range(5):
        s = r[r.dow == w]
        print(f"{DOW[w]:>4}{len(s):>6}{s.cc.mean():>13.3f}%{(s.cc > 0).mean()*100:>6.0f}%"
              f"{s.oc.mean():>10.3f}%{s.co.mean():>10.3f}%")
    # the weekend: Monday rows with gap_days >= 3 (prior close = Friday)
    wknd = r[(r.dow == 0) & (r.gap_days >= 3)]
    print("\n== A2. the weekend itself ==")
    print(f"  Fri close -> Mon OPEN (the gap): mean {wknd.co.mean():+.3f}%  "
          f"positive {(wknd.co > 0).mean()*100:.0f}%  (n={len(wknd)})")
    print(f"  Fri close -> Mon CLOSE:          mean {wknd.cc.mean():+.3f}%  "
          f"positive {(wknd.cc > 0).mean()*100:.0f}%")
    worst = wknd.co.min()
    print(f"  worst weekend gap: {worst:+.2f}%")


def strategy_by_weekday(spy_df, vix_at, dates, entries):
    print("\n== B. 2-day condor (pessimistic) grouped by ENTRY weekday ==")
    print(f"{'entry':>6}{'n':>6}{'win%':>8}{'total':>9}{'avg':>8}")
    buckets = {w: [] for w in range(5)}
    for i in entries:
        r = condor_sim(spy_df, dates, i, vix_at, True)
        if r:
            buckets[pd.Timestamp(dates[i]).weekday()].append(r)
    for w in range(5):
        rows = buckets[w]
        if not rows:
            print(f"{DOW[w]:>6}     0")
            continue
        n = len(rows)
        wn = sum(1 for r in rows if r["outcome"] == "win")
        t = sum(r["pnl"] for r in rows)
        print(f"{DOW[w]:>6}{n:>6}{wn/n*100:>7.1f}%{t:>9.0f}{t/n:>8.2f}")


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()} \
        if vix_df is not None and len(vix_df) else {}
    calendar_stats(spy_df)
    dates, entries = _classify_entries(spy_df, vix_at)
    strategy_by_weekday(spy_df, vix_at, dates, entries)


if __name__ == "__main__":
    run()
