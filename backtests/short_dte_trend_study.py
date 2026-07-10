"""backtests/short_dte_trend_study.py -- is there a 1-3DTE play on TRENDING days?

User (2026-07-10): "even in the wrong regime aren't there other plays?" The
condor is a chop instrument; the natural short-DTE structure for a calm UP-trend
is the bull put spread (sell 0.20-delta put, $5 wing, 2 trading days) — SPY just
has to not crash for ~48h. Same harness as short_dte_condor_study: optimistic
(close-marked), pessimistic (intraday touch of the short forces same-day exit),
and pessimistic + 10% credit haircut. Also prints the condor's numbers on the
SAME trending days as the contrast.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from learning.exit_manager import bs_price, EXIT_SLIPPAGE
from backtests.structure_comparison import COMMISSION_PER_LEG
from backtests.short_dte_condor_study import (
    build_legs as condor_legs_sd, net_credit as condor_credit,
    intrinsic as condor_intrinsic, simulate as condor_sim, DTE_TD, TARGET_PCT, WING,
)
from signals.condor_calc import _strike_for_delta
from signals.regime_detector import RegimeDetector

ENTRY_SLIPPAGE = EXIT_SLIPPAGE


def bps_sim(df, dates, i, vol_at, pessimistic, haircut=0.0):
    """Bull put spread lifecycle: short 0.20-delta put + $5 lower wing, 2 td."""
    d0 = dates[i]
    spot0 = float(df.loc[d0, "close"])
    sigma0 = vol_at.get(d0, 18.0) / 100.0
    t0 = (DTE_TD + 1) / 365.0
    sp = _strike_for_delta("put", spot0, t0, sigma0, 0.20)
    lp = sp - WING
    credit = (bs_price("put", spot0, sp, t0, sigma0)
              - bs_price("put", spot0, lp, t0, sigma0) - ENTRY_SLIPPAGE) * (1 - haircut)
    if credit <= 0.05:
        return None
    commission = COMMISSION_PER_LEG * 2 * 2
    max_profit = credit * 100
    for step in range(1, DTE_TD + 1):
        j = i + step
        if j >= len(dates):
            return None
        row = df.loc[dates[j]]
        h, l, c = float(row["high"]), float(row["low"]), float(row["close"])
        expiry_day = step == DTE_TD
        if expiry_day:
            cost = (max(0.0, sp - c) - max(0.0, lp - c)) + EXIT_SLIPPAGE
        else:
            sig = vol_at.get(dates[j], 18.0) / 100.0
            t = (DTE_TD - step + 1) / 365.0
            cost = max(0.0, bs_price("put", c, sp, t, sig)
                       - bs_price("put", c, lp, t, sig)) + EXIT_SLIPPAGE
        pnl = (credit - cost) * 100 - commission
        hit_target = pnl >= TARGET_PCT * max_profit
        forced = pessimistic and l <= sp
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
    yrs = {}
    for r in rows:
        yrs.setdefault(r["date"].year, []).append(r["pnl"])
    neg = sorted(y for y, v in yrs.items() if sum(v) < 0)
    print(f"  {name:36} n={n:<4} win {w/n*100:5.1f}%  total ${t:>8,.0f}  "
          f"avg ${t/n:>6.2f}  cap ${cap:>4.0f}  neg-years {neg or 'none'}")


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()}
    det = RegimeDetector()
    dates = sorted(spy_df.index)
    trend_days = []
    for i, d in enumerate(dates):
        if i < 210 or i > len(dates) - 35:
            continue
        hist = spy_df.loc[dates[max(0, i - 250):i + 1]].copy()
        hist.index = pd.to_datetime(hist.index)
        try:
            r = det.classify(spy_daily_df=hist, vix_current=vix_at.get(d, 16.0),
                             ivr_current=30.0, today=d)
        except Exception:
            continue
        if r.regime.value == "trending_up_calm":
            trend_days.append(i)
    print(f"TRENDING_UP_CALM days: {len(trend_days)}")
    for label, pess, hc in (("bull put spread [optimistic]", False, 0.0),
                            ("bull put spread [pessimistic]", True, 0.0),
                            ("bull put spread [pess+10% haircut]", True, 0.10)):
        rows = [r for i in trend_days if (r := bps_sim(spy_df, dates, i, vix_at, pess, hc))]
        report(label, rows)
    # contrast: the condor forced onto the same trending days
    rows = [r for i in trend_days if (r := condor_sim(spy_df, dates, i, vix_at, True))]
    report("condor on SAME trend days [pess]", rows)


if __name__ == "__main__":
    run()
