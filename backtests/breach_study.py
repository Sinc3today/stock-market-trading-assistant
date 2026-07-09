"""backtests/breach_study.py -- what does daily-CLOSE marking hide on the stop side?

Audit T2#8: positions are marked at the close; the stop-watchdog runs intraday
but backtests only ever saw closes. Using daily HIGH/LOW (exact for the
underlying-touch question — no 5-min data needed), measure over every condor's
holding window:
  - touch-no-close: intraday extreme crossed a short strike but the CLOSE never
    did (invisible to close-only marking; live watchdog would have fired)
  - watchdog-band days: intraday extreme entered the 0.5% stop buffer
  - gap-through: OPEN beyond a short strike (overnight gap — nothing intraday
    could have warned; the 09:15 gap check from T1.4 is the mitigation)
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.structure_comparison import condor_legs, _classify_entries, ENTRY_DTE
from learning.exit_manager import DTE_CLOSE_THRESHOLD

BUFFER = 0.005


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()} \
        if vix_df is not None and len(vix_df) else {}
    dates, entries = _classify_entries(spy_df, vix_at)

    stats = {"n": 0, "touch_no_close": 0, "band_only": 0, "gap_through": 0,
             "clean": 0, "touch_days": 0, "hold_days": 0}
    for i in entries:
        spot = float(spy_df.loc[dates[i], "close"])
        legs = condor_legs(spot, 0.020)
        sp = next(l["strike"] for l in legs if l["action"] == "SELL" and l["type"] == "put")
        sc = next(l["strike"] for l in legs if l["action"] == "SELL" and l["type"] == "call")
        expiry = dates[i] + timedelta(days=ENTRY_DTE)
        first_cross = None          # None | "overnight" | "intraday"
        warned_before = False       # watchdog band entered on an EARLIER day
        banded_seen = False
        recovered_by_close = False  # crossed intraday but closed back inside
        for j in range(i + 1, len(dates)):
            d = dates[j]
            if (expiry - d).days <= DTE_CLOSE_THRESHOLD:
                break
            row = spy_df.loc[d]
            o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
            stats["hold_days"] += 1
            crossed = l <= sp or h >= sc
            if crossed and first_cross is None:
                first_cross = "overnight" if (o <= sp or o >= sc) else "intraday"
                warned_before = banded_seen
                recovered_by_close = sp < c < sc
                break
            if l <= sp * (1 + BUFFER) or h >= sc * (1 - BUFFER):
                banded_seen = True
        stats["n"] += 1
        if first_cross is None:
            stats.setdefault("no_cross", 0)
            stats["no_cross"] += 1
            if banded_seen:
                stats["band_only"] += 1
        else:
            k = f"cross_{first_cross}"
            stats.setdefault(k, 0); stats[k] += 1
            if warned_before:
                stats.setdefault("warned", 0); stats["warned"] += 1
            if first_cross == "intraday" and recovered_by_close:
                stats["touch_no_close"] += 1

    n = stats["n"]
    crosses = stats.get("cross_overnight", 0) + stats.get("cross_intraday", 0)
    print(f"condors: {n} | avg hold days pre-21DTE: {stats['hold_days']/max(1,n):.1f}")
    print(f"short strike never crossed pre-21DTE: {stats.get('no_cross',0)} "
          f"({stats.get('no_cross',0)/n*100:.1f}%)  [of which watchdog-band scares: {stats['band_only']}]")
    print(f"strike crossed: {crosses} ({crosses/n*100:.1f}%) — first crossing:")
    print(f"  overnight gap (watchdog blind; 09:15 gap check catches at open): "
          f"{stats.get('cross_overnight',0)} ({stats.get('cross_overnight',0)/max(1,crosses)*100:.0f}% of crossings)")
    print(f"  intraday (5-min watchdog catches same day): "
          f"{stats.get('cross_intraday',0)} ({stats.get('cross_intraday',0)/max(1,crosses)*100:.0f}%)")
    print(f"  had PRIOR watchdog-band warning on an earlier day: "
          f"{stats.get('warned',0)} ({stats.get('warned',0)/max(1,crosses)*100:.0f}% of crossings)")
    print(f"  crossed intraday but CLOSED back inside (close-only marking blind): "
          f"{stats['touch_no_close']}")


if __name__ == "__main__":
    run()
