"""backtests/dipbuy_breakdown_study.py -- 50d-low breakdown as a 2nd dip-buy trigger.

The trend-follow study found SPY bounces +1.65/2.51% (68-70% up) after breaking a
new 50-day low — a "buy weakness" signal, more frequent than RSI<30 (n=80 vs 34).
This vets it as a candidate SECOND dip-buy trigger: option-prices it (same bull
debit as the live dip-buy), checks how much it OVERLAPS the RSI<30 trigger
(complementary vs redundant), and reports per-year consistency. Research only.
Run: python -m backtests.dipbuy_breakdown_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.trend_follow_study import donchian_breakout


def breakdown_triggers(spy_df: pd.DataFrame, window: int = 50) -> pd.Series:
    """Fresh Donchian breakdown: close below the prior `window`-day low."""
    return donchian_breakout(spy_df["close"].astype(float), window=window, direction="down")


def trigger_overlap(a: pd.Series, b: pd.Series, within: int = 3) -> dict:
    """How much do two trigger series coincide: same-day, and within N trading
    days (are they the same signal or complementary?)."""
    a = a.fillna(False); b = b.fillna(False)
    a_dates = list(a.index[a])
    b_dates = set(b.index[b])
    b_sorted = sorted(b_dates)
    same = sum(1 for d in a_dates if d in b_dates)
    near = 0
    for d in a_dates:
        lo = d - pd.Timedelta(days=within * 2)  # calendar buffer for `within` td
        hi = d + pd.Timedelta(days=within * 2)
        if any(lo <= bd <= hi for bd in b_sorted):
            near += 1
    return {"a_n": len(a_dates), "b_n": len(b_dates), "same_day": same, "within": near}


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    from backtests.dipbuy_option_wf import load_vix_at, price_dip_trades, summarize
    spy = load_spy()
    vix_at = load_vix_at()
    bd = breakdown_triggers(spy, window=50)
    rsi = _triggers_for(spy, "oversold")

    bd_trades  = price_dip_trades(spy, vix_at, bd)
    rsi_trades = price_dip_trades(spy, vix_at, rsi)
    # union (either trigger), dedup by entry_date
    seen, union = set(), []
    for t in sorted(bd_trades + rsi_trades, key=lambda x: x["entry_date"]):
        if t["entry_date"] in seen:
            continue
        seen.add(t["entry_date"]); union.append(t)

    bs, rs, us = summarize(bd_trades), summarize(rsi_trades), summarize(union)
    print(f"50d-low breakdown vs RSI<30 dip-buy — bull debit, {spy.index.min().date()}..{spy.index.max().date()}\n")
    for name, s in (("breakdown(50d)", bs), ("oversold(RSI<30)", rs), ("union", us)):
        print(f"  {name:>16}: n={s['n']:>3}  mean=${s['mean_pnl']:>7}  win={s['win_rate']:.0%}  total=${s['total_pnl']}")
    ov = trigger_overlap(bd, rsi, within=3)
    print(f"\n  overlap: breakdown n={ov['a_n']}, oversold n={ov['b_n']}, "
          f"same-day={ov['same_day']}, within-3d={ov['within']} "
          f"→ {'COMPLEMENTARY' if ov['within'] < ov['a_n']*0.5 else 'largely REDUNDANT'}")
    print("\n  breakdown per-year P&L:")
    import collections
    py = collections.defaultdict(list)
    for t in bd_trades:
        py[t["entry_year"]].append(t["pnl_dollars"])
    for y in sorted(py):
        v = py[y]; print(f"    {y}: n={len(v):>2}  total=${int(sum(v)):>+5}  mean=${sum(v)/len(v):>+7.0f}")


if __name__ == "__main__":
    main()
