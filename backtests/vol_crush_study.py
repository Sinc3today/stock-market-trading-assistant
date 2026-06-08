"""backtests/vol_crush_study.py -- is there an edge in the post-vol-crush window?

The "sell the news" / vol-crush hypothesis without an error-prone FOMC/CPI date
table: detect the PHENOMENON directly — a sharp VIX drop from an ELEVATED level
(uncertainty resolving, which FOMC/CPI cause + other resolutions). Then test the
post-crush window: does SPY rally (relief, good for long/dip-buy) and does
realized vol FALL (calm, good for condors / premium selling)?

If post-crush = up + calm, that's an OFFENSE case (lean into condors + dip-buys
right after a vol crush) vs only defending around events. Research only.
Run: python -m backtests.vol_crush_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def crush_events(vix: pd.Series, drop_pct: float = -0.10, min_prior: float = 20.0,
                 window: int = 2) -> pd.Series:
    """Fresh vol-crush: VIX fell >= `drop_pct` over `window` days AND was
    elevated (>= min_prior) `window` days ago. Dedupes consecutive crush days."""
    chg = vix.pct_change(window)
    prior = vix.shift(window)
    crush = (chg <= drop_pct) & (prior >= min_prior)
    fresh = crush & ~crush.shift(1).fillna(False)
    return fresh.fillna(False)


def _realized_vol(returns: pd.Series) -> float:
    """Stdev of daily % returns (raw, not annualized)."""
    return float(returns.std()) if len(returns) > 1 else 0.0


def post_crush_window(spy_df: pd.DataFrame, crush_dates, horizon: int = 5) -> dict:
    """For each crush date T: SPY forward `horizon`-day return and forward
    realized vol (T+1..T+h), vs the unconditional baseline."""
    close = spy_df["close"].astype(float)
    daily_ret = close.pct_change() * 100
    pos = {d: i for i, d in enumerate(close.index)}
    fwd_r, fwd_v = [], []
    for d in crush_dates:
        i = pos.get(pd.Timestamp(d))
        if i is None or i + horizon >= len(close):
            continue
        fwd_r.append((close.iloc[i + horizon] / close.iloc[i] - 1) * 100)
        fwd_v.append(_realized_vol(daily_ret.iloc[i + 1: i + 1 + horizon]))
    n = len(fwd_r)
    base_fwd = (close.shift(-horizon) / close - 1) * 100
    base_vol = daily_ret.rolling(horizon).std()
    return {
        "n":                    n,
        "fwd_mean":             round(sum(fwd_r) / n, 4) if n else 0.0,
        "fwd_pct_up":           round(sum(1 for x in fwd_r if x > 0) / n * 100, 1) if n else 0.0,
        "baseline_fwd":         round(float(base_fwd.dropna().mean()), 4),
        "fwd_realized_vol":     round(sum(fwd_v) / n, 4) if n else 0.0,
        "baseline_realized_vol": round(float(base_vol.dropna().mean()), 4),
    }


def main():
    from backtests.dipbuy_signal_study import load_spy
    spy = load_spy()
    vp = os.path.join(os.path.dirname(__file__), "vix_history.csv")
    vix = pd.read_csv(vp, index_col=0, parse_dates=True).sort_index()
    vix = vix[("value" if "value" in vix.columns else vix.columns[-1])].astype(float)
    # align vix to SPY trading days
    vix = vix.reindex(spy.index.union(vix.index)).ffill().reindex(spy.index)

    ev = crush_events(vix, drop_pct=-0.10, min_prior=20.0, window=2)
    crush_dates = list(spy.index[ev.values])
    print(f"Post vol-crush study — SPY {spy.index.min().date()}..{spy.index.max().date()}")
    print(f"crush = VIX −10%+ over 2d from an elevated (≥20) level → {len(crush_dates)} events\n")
    for h in (3, 5, 10):
        r = post_crush_window(spy, crush_dates, horizon=h)
        rally = "UP (relief)" if r["fwd_mean"] > r["baseline_fwd"] else "≤ baseline"
        calm = "CALMER" if r["fwd_realized_vol"] < r["baseline_realized_vol"] else "not calmer"
        print(f"  h={h:>2}: n={r['n']}  fwd={r['fwd_mean']:+.3f}% (base {r['baseline_fwd']:+.3f}%, {rally}, {r['fwd_pct_up']:.0f}% up)"
              f"  | realized vol {r['fwd_realized_vol']:.3f} vs base {r['baseline_realized_vol']:.3f} → {calm}")


if __name__ == "__main__":
    main()
