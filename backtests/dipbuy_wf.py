"""backtests/dipbuy_wf.py -- expanding-window OOS confirmation for the dip-buy.

The oversold dip-buy rule is PARAMETER-FREE (fixed RSI<30 trigger, fixed
ATM/2.5% bull-debit, fixed 21DTE/50%/~10td exit), so a walk-forward has nothing
to FIT — there is no in-sample/OOS overfitting in the usual sense. This module
still provides the spec's expanding-window OOS artifact: burn in the first N
distinct trade-years as "train", aggregate every later year as out-of-sample,
and apply the project's standard verdict gates. It confirms the edge isn't an
artifact of pooling all years at once — but note OOS here = the LATER years,
which are also where the result is strongest (the recency-loading caveat).

Research only. Run: python -m backtests.dipbuy_wf
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# OOS gates (mirror the intraday WF verdict thresholds).
WF_MIN_OOS_WIN_RATE = 0.50
WF_MIN_OOS_PNL      = 0.0
WF_MIN_OOS_SHARPE   = 0.0
WF_MIN_OOS_YEAR_FRAC = config.DIPBUY_MIN_OOS_YEAR_FRAC
WF_MIN_TRAIN_YEARS  = 3


def expanding_oos_folds(trades: list[dict], min_train_years: int = WF_MIN_TRAIN_YEARS) -> dict:
    """Burn in the first `min_train_years` distinct trade-years; return
    {test_year: [pnls]} for every later year (the out-of-sample folds)."""
    years = sorted({t["entry_year"] for t in trades})
    oos_years = set(years[min_train_years:])
    folds: dict[int, list] = {}
    for t in trades:
        if t["entry_year"] in oos_years:
            folds.setdefault(t["entry_year"], []).append(t["pnl_dollars"])
    return folds


def oos_metrics(folds: dict) -> dict:
    """Pool all OOS folds → n, mean/std P&L, Sharpe-like (mean/std), win rate,
    and the fraction of OOS years with positive mean P&L."""
    pnls = [p for fold in folds.values() for p in fold]
    n = len(pnls)
    if not n:
        return {"n": 0, "mean_pnl": 0.0, "std_pnl": 0.0, "sharpe": 0.0,
                "win_rate": 0.0, "pos_year_frac": 0.0, "per_year": {}}
    mean = sum(pnls) / n
    var  = sum((p - mean) ** 2 for p in pnls) / n
    std  = var ** 0.5
    per_year = {y: round(sum(f) / len(f), 2) for y, f in folds.items()}
    pos_years = sum(1 for v in per_year.values() if v > 0)
    return {
        "n":            n,
        "mean_pnl":     round(mean, 2),
        "std_pnl":      round(std, 2),
        "sharpe":       round(mean / std, 3) if std else 0.0,
        "win_rate":     round(sum(1 for p in pnls if p > 0) / n, 3),
        "pos_year_frac": round(pos_years / len(per_year), 3),
        "per_year":     per_year,
    }


def wf_verdict(m: dict) -> dict:
    """PASS iff the pooled OOS folds clear the standard gates."""
    passes = bool(
        m.get("n", 0) >= config.DIPBUY_MIN_TOTAL_TRIGGERS
        and m.get("mean_pnl", 0.0) > WF_MIN_OOS_PNL
        and m.get("win_rate", 0.0) >= WF_MIN_OOS_WIN_RATE
        and m.get("sharpe", 0.0) > WF_MIN_OOS_SHARPE
        and m.get("pos_year_frac", 0.0) >= WF_MIN_OOS_YEAR_FRAC
    )
    return {"passes": passes,
            "oos_mean_pnl": m.get("mean_pnl", 0.0),
            "oos_win_rate": m.get("win_rate", 0.0),
            "oos_sharpe":   m.get("sharpe", 0.0),
            "oos_pos_year_frac": m.get("pos_year_frac", 0.0)}


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    from backtests.dipbuy_option_wf import load_vix_at, price_dip_trades
    spy = load_spy()
    trig = _triggers_for(spy, "oversold")
    trades = price_dip_trades(spy, load_vix_at(), trig)   # face IV
    folds = expanding_oos_folds(trades)
    m = oos_metrics(folds)
    v = wf_verdict(m)
    train_years = sorted({t["entry_year"] for t in trades})[:WF_MIN_TRAIN_YEARS]
    print(f"Dip-buy expanding-window OOS (oversold, parameter-free)")
    print(f"  train burn-in years: {train_years}")
    print(f"  OOS: n={m['n']} mean=${m['mean_pnl']} win={m['win_rate']:.0%} "
          f"sharpe={m['sharpe']} pos-years={m['pos_year_frac']:.0%}")
    print(f"  per-OOS-year: {m['per_year']}")
    print(f"  → verdict: {v}")
    return {"metrics": m, "verdict": v}


if __name__ == "__main__":
    main()
