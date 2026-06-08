"""backtests/trend_follow_study.py -- Donchian trend-follow signal study.

The last open directional corner. Study C: momentum works in high-vol; the bot
only mean-reverts (dip-buy long) and skips trends. Tests whether a Donchian
breakout (close above the prior N-day high = up; below the N-day low = down)
predicts CONTINUATION beyond SPY's baseline drift, and whether it's stronger in
high-vol (per Study C). Forward 10/20d (trend horizons). Underlying-only,
research only. Reuses the dip-buy edge machinery.

Crucial: SPY drifts up, so ANY long signal looks positive. The edge question is
whether the breakout's forward return EXCEEDS the baseline drift (edge vs all
days), not whether it's merely positive. Run: python -m backtests.trend_follow_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.dipbuy_signal_study import forward_returns, edge_vs_baseline, per_year_edges

HORIZONS = (10, 20)


def donchian_breakout(close: pd.Series, window: int = 50, direction: str = "up") -> pd.Series:
    """Fresh Donchian breakout: close crosses beyond the prior `window`-day
    extreme today, but didn't yesterday (dedupes the persistent state)."""
    if direction == "up":
        level = close.rolling(window).max().shift(1)
        beyond = close > level
    else:
        level = close.rolling(window).min().shift(1)
        beyond = close < level
    fresh = beyond & ~beyond.shift(1).fillna(False)
    return fresh.fillna(False)


def run_trend_arm(spy_df: pd.DataFrame, direction: str = "up", window: int = 50) -> dict:
    """Forward-return edge of a Donchian breakout vs baseline, per horizon.
    For 'down', a short profits on negative returns, so we also report short_edge
    = baseline − conditional."""
    close = spy_df["close"].astype(float)
    trig  = donchian_breakout(close, window=window, direction=direction)
    by_h = {}
    for h in HORIZONS:
        fwd   = forward_returns(close, h)
        stats = edge_vs_baseline(fwd, trig)
        pye   = per_year_edges(fwd, trig)
        by_h[h] = {**stats, "per_year": pye,
                   "short_edge": round(stats["baseline_mean"] - stats["cond_mean"], 4)}
    return {"direction": direction, "window": window, "by_horizon": by_h}


def _load_vix():
    vp = os.path.join(os.path.dirname(__file__), "vix_history.csv")
    df = pd.read_csv(vp, index_col=0, parse_dates=True).sort_index()
    return df[("value" if "value" in df.columns else df.columns[-1])].astype(float)


def vol_gated_edge(spy_df, direction="up", window=50, horizon=10, vix_threshold=18.0):
    """Conditional forward return for the breakout, split by VIX bucket at entry."""
    close = spy_df["close"].astype(float)
    trig  = donchian_breakout(close, window=window, direction=direction)
    fwd   = forward_returns(close, horizon)
    vix   = _load_vix()
    out = {}
    for label, hi in (("calm", False), ("high", True)):
        mask = pd.Series(False, index=close.index)
        for d in close.index[trig & fwd.notna()]:
            v = float(vix.reindex(vix.index.union([d])).ffill().get(d, float("nan")))
            if pd.notna(v) and ((v >= vix_threshold) == hi):
                mask.loc[d] = True
        cond = fwd[mask]
        n = len(cond)
        out[label] = {"n": n, "cond_mean": round(float(cond.mean()), 4) if n else 0.0,
                      "pct_positive": round(float((cond > 0).mean()) * 100, 1) if n else 0.0}
    return out


def main():
    from backtests.dipbuy_signal_study import load_spy
    spy = load_spy()
    base20 = round(float(forward_returns(spy["close"].astype(float), 20).mean()), 3)
    print(f"Donchian trend-follow — SPY {spy.index.min().date()}..{spy.index.max().date()} "
          f"(baseline 20d drift {base20:+.3f}%)\n")
    print(f"{'arm':>14}{'h':>4}{'n':>5}{'cond%':>9}{'base%':>8}{'edge%':>8}{'pos%':>7}  read")
    for direction in ("up", "down"):
        res = run_trend_arm(spy, direction=direction, window=50)
        for h, s in res["by_horizon"].items():
            if direction == "up":
                read = "momentum edge" if s["edge"] > 0.1 else "just drift / no edge"
            else:
                read = "SHORT edge (continues down)" if s["short_edge"] > 0.1 and s["cond_mean"] < 0 else "no short edge (bounces)"
            print(f"{('breakout_'+direction):>14}{h:>4}{s['n']:>5}{s['cond_mean']:>9.3f}"
                  f"{s['baseline_mean']:>8.3f}{s['edge']:>8.3f}{s['pct_positive']:>6.0f}%  {read}")
    print("\nVol gate (breakout_up, 10d — is momentum stronger in high-vol per Study C?):")
    g = vol_gated_edge(spy, direction="up", window=50, horizon=10)
    print(f"  calm<18: n={g['calm']['n']} {g['calm']['cond_mean']:+.3f}%  | "
          f"high>=18: n={g['high']['n']} {g['high']['cond_mean']:+.3f}%")
    print("Vol gate (breakout_down, 10d):")
    g = vol_gated_edge(spy, direction="down", window=50, horizon=10)
    print(f"  calm<18: n={g['calm']['n']} {g['calm']['cond_mean']:+.3f}%  | "
          f"high>=18: n={g['high']['n']} {g['high']['cond_mean']:+.3f}%")


if __name__ == "__main__":
    main()
