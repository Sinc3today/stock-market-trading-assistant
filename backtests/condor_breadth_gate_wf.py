"""backtests/condor_breadth_gate_wf.py -- does a sector-BREADTH gate protect the
calm iron condor out-of-sample, or is it redundant with the VIX<18 filter?

The breadth study (sector_breadth_study) found low/falling breadth -> ~1.5-2x
forward realized vol = condor breach risk. The calm condor (CHOPPY_LOW_VOL)
already requires VIX < 18, though, and low breadth usually co-occurs with a
selloff = elevated VIX. So the real, falsifiable question is the HYG-filter
question again: among days that ALREADY pass the calm gate, does breadth add
ORTHOGONAL information about breach risk, or does VIX already capture it?

We price the production iron condor on every tradeable CHOPPY_LOW_VOL (+ optional
CHOPPY_TRANSITION) day with the same realistic BS-lifecycle engine the live paper
trader marks with, then compare:

  baseline            : every calm-condor day (current behavior)
  +breadth floor      : trade only when 50d-breadth >= a floor (high participation)
  +breadth not-falling: skip days where 50d-breadth fell >=15pts over 10d

Split into in-sample (first 60% of entry days) and OOS (last 40%). A gate that
only helps in-sample was curve-fitting. We also report the breadth<->VIX
correlation on condor days (the redundancy tell) and the loss tail (breach
proxy). Research only -- no source/threshold changes here.

Run: python -m backtests.condor_breadth_gate_wf
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from loguru import logger

OOS_FRACTION = 0.60   # first 60% of entry days = in-sample
BREACH_LOSS  = -150   # a condor loss worse than this $ = "breach" proxy


def gate_keep(entry_dates, breadth: pd.Series, mode: str = "none",
              threshold: float = 50.0, drop: float = 15.0, window: int = 10) -> list:
    """Filter condor entry dates by a breadth rule. Pure.

    mode 'none'        -> keep all.
    mode 'floor'       -> keep days with breadth >= threshold.
    mode 'not_falling' -> drop days where breadth fell >= `drop` pts over `window`.
    Dates with no breadth datum are conservatively skipped (not traded blind)."""
    if mode == "none":
        return list(entry_dates)
    kept = []
    for d in entry_dates:
        d = pd.Timestamp(d)
        if d not in breadth.index:
            continue
        bt = breadth.loc[d]
        if pd.isna(bt):
            continue
        if mode == "floor":
            if bt >= threshold:
                kept.append(d)
        elif mode == "not_falling":
            i = breadth.index.get_loc(d)
            prior = breadth.iloc[i - window] if i >= window else None
            if prior is None or pd.isna(prior) or bt > prior - drop:
                kept.append(d)
        else:
            kept.append(d)
    return kept


def _metrics(trades):
    if not trades:
        return {"n": 0, "win": 0.0, "avg": 0.0, "edge": 0.0, "total": 0,
                "avg_loss": 0.0, "breach": 0.0}
    pnl = np.array([t["pnl_dollars"] for t in trades], dtype=float)
    wins = (pnl > 0).sum()
    losses = pnl[pnl < 0]
    edge = float(np.mean(pnl) / (np.std(pnl) + 1e-9))
    return {
        "n": len(pnl), "win": round(wins / len(pnl) * 100, 1),
        "avg": round(float(np.mean(pnl)), 1), "edge": round(edge, 3),
        "total": int(pnl.sum()),
        "avg_loss": round(float(losses.mean()), 1) if len(losses) else 0.0,
        "breach": round(float((pnl < BREACH_LOSS).mean()) * 100, 1),  # % of trades
    }


def _row(label, window, m):
    return (f"  {label:<22} {window:<11} {m['n']:>4} {m['win']:>5.1f}% "
            f"${m['avg']:>+6.0f} {m['edge']:>+7.3f} | avg_loss ${m['avg_loss']:>+6.0f} "
            f"breach {m['breach']:>4.1f}%")


def main():
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from backtests.realistic_pricing import simulate_trade, _vix_lookup
    from backtests.sector_breadth_study import pct_above_ma, load_sector_panel
    from data.event_calendar import EventCalendar
    from signals.regime_detector import Regime

    loader = BacktestDataLoader()
    spy_df, vix_df = loader.load(years=5, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()
    regime_df["date"] = pd.to_datetime(regime_df["date"])

    condor_regimes = {Regime.CHOPPY_LOW_VOL.value, Regime.CHOPPY_TRANSITION.value}
    cond = regime_df[(regime_df["regime"].isin(condor_regimes)) &
                     (regime_df["tradeable"] == True)].sort_values("date")
    entry_dates = list(cond["date"])
    if not entry_dates:
        print("No tradeable calm-condor days in the sample."); return

    # breadth aligned to SPY trading days
    spy2 = spy_df.copy(); spy2.index = pd.to_datetime(spy2.index)
    panel = load_sector_panel()
    panel = {t: s.reindex(spy2.index.union(s.index)).ffill().reindex(spy2.index)
             for t, s in panel.items()}
    breadth = pct_above_ma(panel, 50)
    q = breadth.dropna()
    floor_thr = round(float(q.quantile(0.50)), 1)   # trade only above-median participation

    dates = sorted(pd.to_datetime(spy2.index))
    didx = {d: i for i, d in enumerate(dates)}
    va = _vix_lookup(dates, vix_df)

    def price(date_list):
        out = []
        for d in date_list:
            d = pd.Timestamp(d)
            if d not in didx:
                continue
            r = simulate_trade(spy2, dates, didx[d], "iron_condor", va)
            if r:
                r["date"] = d
                out.append(r)
        return out

    variants = {
        "baseline (all calm)":        entry_dates,
        f"+breadth floor>={floor_thr:.0f}%":  gate_keep(entry_dates, breadth, "floor", threshold=floor_thr),
        "+breadth not-falling":       gate_keep(entry_dates, breadth, "not_falling", drop=15, window=10),
    }

    boundary = entry_dates[int(len(entry_dates) * OOS_FRACTION)]
    print("\n" + "=" * 84)
    print("  BREADTH GATE on the CALM IRON CONDOR -- IN-SAMPLE vs OUT-OF-SAMPLE")
    print("=" * 84)
    print(f"  calm-condor tradeable days: {len(entry_dates)} "
          f"({entry_dates[0].date()} -> {entry_dates[-1].date()})  | IS < {boundary.date()} <= OOS")

    # redundancy tell: breadth vs VIX on condor days
    cd_b = breadth.reindex(pd.to_datetime(entry_dates)).values
    cd_v = np.array([va.get(pd.Timestamp(d), np.nan) for d in entry_dates])
    msk = ~(np.isnan(cd_b) | np.isnan(cd_v))
    if msk.sum() > 2:
        corr = float(np.corrcoef(cd_b[msk], cd_v[msk])[0, 1])
        print(f"  breadth<->VIX corr on condor days: {corr:+.2f}  "
              f"(strongly negative => breadth ~ redundant with the VIX<18 gate)")

    print(f"\n  {'variant':<22} {'window':<11} {'n':>4} {'win%':>6} {'$/trd':>7} {'edge':>7}")
    summary = {}
    for name, dl in variants.items():
        trades = price(dl)
        ins = [t for t in trades if t["date"] < boundary]
        oos = [t for t in trades if t["date"] >= boundary]
        summary[name] = (_metrics(ins), _metrics(oos))
        print("-" * 84)
        print(_row(name, "in-sample", _metrics(ins)))
        print(_row("", "out-sample", _metrics(oos)))

    base_oos = summary["baseline (all calm)"][1]
    print("\n" + "=" * 84)
    print("  VERDICT (OOS vs baseline OOS)")
    for name, (ins, oos) in summary.items():
        if name.startswith("baseline"):
            continue
        d_win = oos["win"] - base_oos["win"]
        d_edge = oos["edge"] - base_oos["edge"]
        d_breach = oos["breach"] - base_oos["breach"]
        kept = oos["n"] / base_oos["n"] * 100 if base_oos["n"] else 0
        note = ("HELPS" if d_edge > 0.03 and d_breach <= 0 else
                "no OOS improvement" if d_edge <= 0.03 else "mixed")
        print(f"    {name:<24} keeps {kept:>3.0f}% of days | "
              f"win {d_win:+.1f}pp  edge {d_edge:+.3f}  breach {d_breach:+.1f}pp -> {note}")
    print("=" * 84 + "\n")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    main()
