"""backtests/skew_stress.py -- does the condor edge survive worse credits?

Audit T2#6: our BS pricing uses flat vol (VIX for every strike) — no skew, r=0.
Real-world condor credits and exits differ from the model. The honest stress:
re-run the exact condor backtest with the ENTRY CREDIT haircut by 0/10/15/20%
(same entry days, same management, same exit marks) and see where the edge dies.

The haircut compounds: lower credit = lower max profit AND higher max loss
(capital), so both win economics and the profit-target trigger move.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import timedelta

import pandas as pd

from learning.exit_manager import bs_price, PROFIT_TARGET_PCT, DTE_CLOSE_THRESHOLD, EXIT_SLIPPAGE
from backtests.structure_comparison import (
    condor_legs, _net_debit_bs, _value_at_expiry, _wing_width,
    _classify_entries, ENTRY_DTE, COMMISSION_PER_LEG, ENTRY_SLIPPAGE,
)


def simulate_haircut(legs, spy_df, dates, entry_idx, vix_at, haircut: float):
    """structure_comparison.simulate, with the entry credit reduced by
    `haircut` (0.0-1.0). Exit marks stay model-priced — we're stressing what we
    RECEIVE, the conservative direction."""
    entry_date = dates[entry_idx]
    expiry = entry_date + timedelta(days=ENTRY_DTE)
    spot0 = float(spy_df.loc[entry_date, "close"])
    vix0 = vix_at.get(entry_date, 16.0)
    nd0 = _net_debit_bs(legs, spot0, vix0 / 100.0, ENTRY_DTE / 365.0)
    if nd0 >= 0:          # not a credit -> skip (shouldn't happen for condors)
        return None
    width = _wing_width(legs)
    commission = COMMISSION_PER_LEG * len(legs) * 2

    credit = (-nd0 - ENTRY_SLIPPAGE) * (1.0 - haircut)
    if credit <= 0:
        return None
    entry_eff = -credit
    max_profit = credit * 100
    capital = max(0.01, (width - credit)) * 100

    for j in range(entry_idx + 1, len(dates)):
        d = dates[j]
        dte = (expiry - d).days
        spot = float(spy_df.loc[d, "close"])
        if dte <= 0:
            cur = _value_at_expiry(legs, spot)
        else:
            cur = _net_debit_bs(legs, spot, vix_at.get(d, vix0) / 100.0, max(dte, 0) / 365.0)
        pnl = (cur - entry_eff) * 100 - EXIT_SLIPPAGE * 100
        hit_target = max_profit > 0 and pnl / max_profit >= PROFIT_TARGET_PCT
        if hit_target or dte <= DTE_CLOSE_THRESHOLD or dte <= 0:
            net = pnl - commission
            return {"pnl": round(net, 2),
                    "outcome": "win" if net > 0 else "loss" if net < 0 else "be",
                    "capital": round(capital, 2)}
    return None


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {}
    if vix_df is not None and len(vix_df):
        vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()}
    dates, entries = _classify_entries(spy_df, vix_at)
    print(f"Entry days: {len(entries)}")
    print(f"\n{'haircut':>8}{'n':>6}{'win%':>8}{'totP&L':>10}{'avg':>8}{'avgCap':>9}{'ret/cap':>9}")
    for h in (0.0, 0.10, 0.15, 0.20, 0.25):
        rows = []
        for i in entries:
            spot = float(spy_df.loc[dates[i], "close"])
            r = simulate_haircut(condor_legs(spot, 0.020), spy_df, dates, i, vix_at, h)
            if r:
                rows.append(r)
        n = len(rows)
        if not n:
            continue
        wins = sum(1 for r in rows if r["outcome"] == "win")
        tot = sum(r["pnl"] for r in rows)
        cap = sum(r["capital"] for r in rows) / n
        print(f"{h*100:>7.0f}%{n:>6}{wins/n*100:>7.1f}%{tot:>10.0f}{tot/n:>8.2f}"
              f"{cap:>9.0f}{(tot/n)/cap*100:>8.1f}%")


if __name__ == "__main__":
    run(years=5)
