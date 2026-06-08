"""backtests/overbought_short_study.py -- overbought-short signal study (Phase 1).

Mirror of the oversold dip-buy: does SPY mean-revert DOWN after a fresh RSI>70
overbought cross, the way it bounces UP after RSI<30? If so, a bear-debit short
has an edge. Honest prior: in a mostly-bull 15yr tape, overbought tends to
PERSIST (momentum), so the short likely has no edge — but it's a cheap, clean
test that maps the short side. Reuses the dip-buy edge machinery. Research only.

A SHORT profits when the forward return is NEGATIVE, so short_edge = baseline −
conditional (positive when SPY reverts down more than usual). Run:
    python -m backtests.overbought_short_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

import config
from backtests.dipbuy_signal_study import rsi_series, forward_returns, per_year_edges


def overbought_triggers(rsi: pd.Series, threshold: float = 70.0) -> pd.Series:
    """Fresh cross ABOVE threshold: today > t, yesterday <= t (mirror of oversold)."""
    above = rsi > threshold
    prev_not_above = ~(rsi.shift(1) > threshold)
    return (above & prev_not_above).fillna(False)


def run_overbought(spy_df: pd.DataFrame) -> dict:
    close = spy_df["close"].astype(float)
    trig  = overbought_triggers(rsi_series(close, 14), 70.0)
    by_h = {}
    for h in config.DIPBUY_FWD_HORIZONS:
        fwd   = forward_returns(close, h)
        valid = fwd.notna()
        base  = fwd[valid]
        cond  = fwd[valid & trig.reindex(fwd.index, fill_value=False)]
        n = int(len(cond))
        cond_mean = float(cond.mean()) if n else 0.0
        base_mean = float(base.mean()) if len(base) else 0.0
        pye = per_year_edges(fwd, trig)
        by_h[h] = {
            "n":          n,
            "cond_mean":  round(cond_mean, 4),          # SPY fwd return after overbought
            "baseline":   round(base_mean, 4),
            "short_edge": round(base_mean - cond_mean, 4),  # >0 ⇒ reverts down ⇒ short works
            "pct_down":   round(float((cond < 0).mean()) * 100, 1) if n else 0.0,
            "per_year":   pye,
        }
    return {"by_horizon": by_h}


def main():
    from backtests.dipbuy_signal_study import load_spy
    spy = load_spy()
    res = run_overbought(spy)
    print(f"Overbought-short signal study — SPY {spy.index.min().date()}..{spy.index.max().date()}")
    print("(short profits when SPY forward return is NEGATIVE; short_edge>0 ⇒ reverts down)\n")
    print(f"{'h':>3}{'n':>5}{'SPYfwd%':>9}{'base%':>8}{'short_edge%':>12}{'down%':>7}  read")
    for h, s in res["by_horizon"].items():
        read = ("SHORT edge" if s["short_edge"] > 0.05 and s["cond_mean"] < 0
                else "momentum (keeps rising)" if s["cond_mean"] > s["baseline"]
                else "no edge")
        print(f"{h:>3}{s['n']:>5}{s['cond_mean']:>9.3f}{s['baseline']:>8.3f}"
              f"{s['short_edge']:>12.3f}{s['pct_down']:>6.0f}%  {read}")
    return res


if __name__ == "__main__":
    main()
