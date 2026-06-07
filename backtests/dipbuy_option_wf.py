"""backtests/dipbuy_option_wf.py -- Phase 2 option-priced dip-buy walk-forward.

Runs ONLY for the oversold arm that survived Phase 1 (see dipbuy_signal_study).
Prices a bull call debit spread on each oversold trigger (~21 DTE, 50% profit
target or ~10-trading-day time-close) via backtests.realistic_pricing, then asks
whether the option structure captures the bounce profitably and ROBUSTLY:
per-year positive, both chronological halves positive, AND surviving an IV-stress
arm (entry IV bumped on these down-tape entries — the flat-VIX BS model
understates crash-time option cost). Research only; no live wiring.

Spec: docs/superpowers/specs/2026-06-07-dipbuy-directional-study-design.md
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

import config
from backtests.realistic_pricing import simulate_trade

# Bounce-shaped exit: short-dated debit, take the 50% pop or time out ~10 td.
ENTRY_DTE_DIP        = 21
PROFIT_TARGET_DIP    = 0.50
DTE_CLOSE_DIP        = 7      # close ~10 trading days in (≈14 cal days remaining)

_VIX_CSV = os.path.join(os.path.dirname(__file__), "vix_history.csv")


def load_vix_at(path: str = _VIX_CSV) -> dict:
    """date -> VIX value, from the refreshed CBOE history (columns date,value)."""
    vx = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    col = "value" if "value" in vx.columns else vx.columns[0]
    return {d: float(vx.loc[d, col]) for d in vx.index}


def price_dip_trades(spy_df: pd.DataFrame, vix_at: dict, triggers: pd.Series,
                     *, stress_mult: float = 1.0) -> list[dict]:
    """Price a bull_debit on each trigger day. stress_mult > 1 bumps ONLY the
    entry day's VIX (raising the debit paid) while later marks use normal VIX —
    capturing both the inflated entry cost and the vol-crush on the way out."""
    dates = list(spy_df.index)
    trig  = triggers.reindex(spy_df.index, fill_value=False)
    out: list[dict] = []
    for i, d in enumerate(dates):
        if not bool(trig.iloc[i]):
            continue
        vat = vix_at
        if stress_mult != 1.0:
            vat = dict(vix_at)
            vat[d] = vix_at.get(d, 16.0) * stress_mult
        res = simulate_trade(
            spy_df, dates, i, "bull_debit", vat,
            entry_dte=ENTRY_DTE_DIP,
            profit_target_pct=PROFIT_TARGET_DIP,
            dte_close_threshold=DTE_CLOSE_DIP,
        )
        if res is None:
            continue   # ran off the end of data — dropped, no silent miscount
        out.append({**res, "entry_date": d, "entry_year": int(d.year)})
    return out


def summarize(trades: list[dict]) -> dict:
    """Aggregate priced trades: n, total/mean P&L, win rate, per-year mean P&L,
    and chronological half-split means (trades sorted by entry date)."""
    n = len(trades)
    if not n:
        return {"n": 0, "total_pnl": 0.0, "mean_pnl": 0.0, "win_rate": 0.0,
                "per_year": {}, "half_means": (0.0, 0.0)}
    ts   = (sorted(trades, key=lambda t: t["entry_date"])
            if all("entry_date" in t for t in trades) else list(trades))
    pnls = [t["pnl_dollars"] for t in ts]
    per_year: dict[int, list] = {}
    for t in ts:
        per_year.setdefault(t["entry_year"], []).append(t["pnl_dollars"])
    half = n // 2
    return {
        "n":          n,
        "total_pnl":  round(sum(pnls), 2),
        "mean_pnl":   round(sum(pnls) / n, 2),
        "win_rate":   round(sum(1 for p in pnls if p > 0) / n, 3),
        "per_year":   {y: round(sum(v) / len(v), 2) for y, v in per_year.items()},
        "half_means": (round(sum(pnls[:half]) / half, 2) if half else 0.0,
                       round(sum(pnls[half:]) / (n - half), 2) if n - half else 0.0),
    }


def phase2_verdict(face: dict, stressed: dict) -> dict:
    """PASS iff the face-IV run is profitable + robust AND the IV-stressed run
    is still profitable. Mirrors the Phase-1 robustness philosophy on dollars."""
    py  = face.get("per_year", {})
    pos = sum(1 for v in py.values() if v > 0)
    frac = (pos / len(py)) if py else 0.0
    half_ok = min(face.get("half_means", (0.0, 0.0))) > 0
    face_ok = bool(
        face.get("n", 0) >= config.DIPBUY_MIN_TOTAL_TRIGGERS
        and face.get("mean_pnl", 0.0) > 0
        and frac >= config.DIPBUY_MIN_OOS_YEAR_FRAC
        and half_ok
    )
    stress_ok = stressed.get("mean_pnl", 0.0) > 0
    return {
        "survives":       bool(face_ok and stress_ok),
        "face_ok":        face_ok,
        "stress_ok":      stress_ok,
        "pos_year_frac":  round(frac, 3),
        "face_mean_pnl":  face.get("mean_pnl", 0.0),
        "stress_mean_pnl": stressed.get("mean_pnl", 0.0),
    }


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    spy = load_spy()
    vix_at = load_vix_at()
    trig = _triggers_for(spy, "oversold")
    face_trades   = price_dip_trades(spy, vix_at, trig, stress_mult=1.0)
    stress_trades = price_dip_trades(spy, vix_at, trig,
                                     stress_mult=config.DIPBUY_IV_STRESS_MULT)
    face   = summarize(face_trades)
    stress = summarize(stress_trades)
    verdict = phase2_verdict(face, stress)
    print(f"Phase 2 — oversold bull-debit, {spy.index.min().date()}..{spy.index.max().date()}")
    print(f"  face-IV : n={face['n']} mean=${face['mean_pnl']} win={face['win_rate']:.0%} "
          f"total=${face['total_pnl']} halves={face['half_means']}")
    print(f"  IV-stress: n={stress['n']} mean=${stress['mean_pnl']} "
          f"win={stress['win_rate']:.0%} total=${stress['total_pnl']}")
    print(f"  per-year (face): {face['per_year']}")
    print(f"  → verdict: {verdict}")
    return {"face": face, "stress": stress, "verdict": verdict}


if __name__ == "__main__":
    main()
