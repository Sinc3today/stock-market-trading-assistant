"""backtests/short_dte_condor_study.py -- is the 1-3DTE condor cycle safe to mirror?

The live 1-3DTE record (9 trades, 5W/0L, +$94 avg) is encouraging but tiny. The
user wants to mirror these with real money — before that, replay the cycle over
5 years for SPY and QQQ:

  - entry on every regime-gated condor day (same CHOPPY gate as live)
  - shorts at ~0.20-delta FOR THE SHORT EXPIRY (condor_calc's solver at dte=2,
    matching how the live OptionsLayer picks strikes), $5 wings
  - expiry 2 trading days out; exit at expiry intrinsic or 70% profit target

Short DTE makes daily-close marking dishonest (gamma lives intraday), so TWO
variants bracket reality:
  optimistic  : marked at closes only (like the older backtests)
  pessimistic : if the day's HIGH/LOW crosses a short strike, force exit at that
                day's CLOSE (models "watchdog fired, user closed same day")
Frictions (slippage + commissions) included — they are proportionally huge on
small short-DTE credits, which is exactly why this test matters.
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from learning.exit_manager import bs_price, EXIT_SLIPPAGE
from backtests.structure_comparison import _classify_entries, COMMISSION_PER_LEG
from signals.condor_calc import _strike_for_delta

ENTRY_SLIPPAGE = EXIT_SLIPPAGE
DTE_TD = 2            # trading days to expiry
TARGET_PCT = 0.70
WING = 5.0


def build_legs(spot, sigma):
    t = (DTE_TD + 1) / 365.0
    sc = _strike_for_delta("call", spot, t, sigma, 0.20)
    sp = _strike_for_delta("put", spot, t, sigma, 0.20)
    return sp, sc, sp - WING, sc + WING


def net_credit(spot, sigma, sp, sc, lp, lc, t):
    return (bs_price("put", spot, sp, t, sigma) + bs_price("call", spot, sc, t, sigma)
            - bs_price("put", spot, lp, t, sigma) - bs_price("call", spot, lc, t, sigma))


def intrinsic(spot, sp, sc, lp, lc):
    return (max(0.0, sp - spot) - max(0.0, lp - spot)
            + max(0.0, spot - sc) - max(0.0, spot - lc))


def simulate(df, dates, i, vol_at, pessimistic):
    d0 = dates[i]
    spot0 = float(df.loc[d0, "close"])
    sigma0 = vol_at.get(d0, 18.0) / 100.0
    sp, sc, lp, lc = build_legs(spot0, sigma0)
    t0 = (DTE_TD + 1) / 365.0
    credit = net_credit(spot0, sigma0, sp, sc, lp, lc, t0) - ENTRY_SLIPPAGE
    if credit <= 0.05:
        return None
    commission = COMMISSION_PER_LEG * 4 * 2
    max_profit = credit * 100
    for step in range(1, DTE_TD + 1):
        j = i + step
        if j >= len(dates):
            return None
        d = dates[j]
        row = df.loc[d]
        h, l, c = float(row["high"]), float(row["low"]), float(row["close"])
        touched = l <= sp or h >= sc
        expiry_day = step == DTE_TD
        if expiry_day:
            cost = intrinsic(c, sp, sc, lp, lc) + EXIT_SLIPPAGE
        else:
            sig = vol_at.get(d, 18.0) / 100.0
            cost = max(0.0, net_credit(c, sig, sp, sc, lp, lc,
                                       (DTE_TD - step + 1) / 365.0)) + EXIT_SLIPPAGE
        pnl = (credit - cost) * 100 - commission
        hit_target = pnl >= TARGET_PCT * max_profit
        forced = pessimistic and touched
        if expiry_day or hit_target or forced:
            return {"pnl": round(pnl, 2), "outcome": "win" if pnl > 0 else "loss",
                    "capital": round((WING - credit) * 100, 2), "date": d0}
    return None


def report(name, rows):
    n = len(rows)
    if not n:
        print(f"  {name}: no trades"); return
    w = sum(1 for r in rows if r["outcome"] == "win")
    t = sum(r["pnl"] for r in rows)
    cap = sum(r["capital"] for r in rows) / n
    worst = min(r["pnl"] for r in rows)
    yrs = {}
    for r in rows:
        yrs.setdefault(r["date"].year, []).append(r["pnl"])
    neg = [y for y, v in yrs.items() if sum(v) < 0]
    print(f"  {name:28} n={n:<4} win {w/n*100:5.1f}%  total ${t:>9,.0f}  avg ${t/n:>7.2f}"
          f"  cap ${cap:>4.0f}  worst ${worst:>7.0f}  neg-years {sorted(neg) or 'none'}")


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    from backtests.qqq_condor_transfer import load_yf
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()}
    dates, entries = _classify_entries(spy_df, vix_at)
    entry_dates = [dates[i] for i in entries]
    print(f"regime-gated condor days: {len(entry_dates)}")

    qqq = load_yf("QQQ", str(spy_df.index[0]))
    vxn = load_yf("^VXN", str(spy_df.index[0]))
    vxn_at = {d: float(c) for d, c in vxn["close"].items()}
    qdates = sorted(qqq.index)
    qidx = {d: k for k, d in enumerate(qdates)}

    for label, df, ddates, didx, vol in (
            ("SPY 1-3DTE condor", spy_df, dates, {d: k for k, d in enumerate(dates)}, vix_at),
            ("QQQ 1-3DTE condor (VXN)", qqq, qdates, qidx, vxn_at)):
        for pess in (False, True):
            rows = []
            for d in entry_dates:
                k = didx.get(d)
                if k is None:
                    continue
                r = simulate(df, ddates, k, vol, pess)
                if r:
                    rows.append(r)
            report(f"{label} [{'pessimistic' if pess else 'optimistic'}]", rows)


if __name__ == "__main__":
    run()
