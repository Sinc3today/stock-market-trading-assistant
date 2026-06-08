"""backtests/condor_transition_study.py -- transition-zone condor sub-condition.

The transition-zone iron condor (VIX 18-22, the CHOPPY_TRANSITION regime) is a
−$2,950/5yr net LOSER but BIMODAL (good 2022/24/26 ~83% win, bad 2023/25 ~43%).
Hypothesis from the vol-gate finding: condors die when vol is EXPANDING. So the
losing transition days should cluster where VIX is RISING. If so, a surgical
"skip transition when VIX rising" filter recovers the loss; if not, it's noise.

Note: the daily backtest uses a synthetic fixed-payoff model — fine for "does the
loss cluster by vol-direction" (driven by whether SPY stayed in range), but treat
absolute P&L as indicative. Research only. Run: python -m backtests.condor_transition_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def vix_direction(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """Add 'vix_rising' = VIX today > VIX `lookback` rows ago (vol expanding)."""
    d = df.sort_values("date").reset_index(drop=True).copy()
    d["vix_rising"] = d["vix"] > d["vix"].shift(lookback)
    return d


def transition_subcondition(df: pd.DataFrame) -> dict:
    """Split tradeable CHOPPY_TRANSITION condor days by VIX direction. Returns
    per-bucket n/pnl/win-rate/mean + per-year rising-vs-falling P&L + the P&L
    recovered by skipping the rising-VIX (vol-expanding) days."""
    d = df[(df["regime"] == "choppy_transition") & (df["tradeable"] == True)].copy()
    d["year"] = [pd.Timestamp(x).year for x in d["date"]]
    out = {}
    for label, mask in (("vix_rising",  d["vix_rising"] == True),
                        ("vix_falling", d["vix_rising"] == False)):
        sub = d[mask]
        n = len(sub)
        out[label] = {
            "n":        n,
            "pnl":      int(sub["pnl"].sum()),
            "win_rate": round((sub["outcome"] == "win").mean() * 100, 1) if n else 0.0,
            "mean":     round(sub["pnl"].mean(), 1) if n else 0.0,
        }
    out["per_year"] = {}
    for y, g in d.groupby("year"):
        out["per_year"][int(y)] = {
            "rising_pnl":  int(g[g["vix_rising"] == True]["pnl"].sum()),
            "falling_pnl": int(g[g["vix_rising"] == False]["pnl"].sum()),
        }
    out["total_pnl"]        = int(d["pnl"].sum())
    out["skip_rising_pnl"]  = out["vix_falling"]["pnl"]   # P&L if we skip rising-VIX days
    return out


def main():
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar
    spy_df, vix_df = BacktestDataLoader().load(years=5, source="local")
    df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()
    df = vix_direction(df, lookback=5)
    res = transition_subcondition(df)
    print("Transition-zone condor (VIX 18-22) sub-condition — VIX direction\n")
    for b in ("vix_rising", "vix_falling"):
        r = res[b]
        print(f"  {b:>12}: n={r['n']:>3}  win={r['win_rate']:>5}%  "
              f"pnl=${r['pnl']:>+6}  mean=${r['mean']:>+7}")
    print(f"\n  total transition P&L: ${res['total_pnl']:+}")
    print(f"  → skip rising-VIX days: ${res['skip_rising_pnl']:+} "
          f"(recovers ${res['skip_rising_pnl'] - res['total_pnl']:+})")
    print("\n  per-year (rising / falling P&L):")
    for y, d2 in sorted(res["per_year"].items()):
        print(f"    {y}: rising ${d2['rising_pnl']:>+6}  falling ${d2['falling_pnl']:>+6}")
    return res


if __name__ == "__main__":
    main()
