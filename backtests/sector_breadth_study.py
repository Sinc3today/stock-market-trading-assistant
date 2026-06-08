"""backtests/sector_breadth_study.py -- is market-breadth deterioration a
tradeable risk signal for SPY?

Hypothesis (from the YouTube/Substack mining lead): when fewer and fewer
sectors participate in the tape — "breadth deterioration" — SPY is more
fragile. We test this DIRECTLY and falsifiably against ~16y of sector ETF
history rather than trusting the narrative.

Breadth proxy: each day, the % of SPDR sector ETFs trading above their own
N-day moving average (the classic participation gauge). Denominator adapts
to whichever sectors have a valid MA that day (XLRE listed 2015, XLC 2018),
so early years use the 9 original sectors and later years all 11.

We then ask three falsifiable questions, tied to the bot's two live edges:

  1. RISK (condor breach): when breadth is LOW / falling, is SPY forward
     realized vol HIGHER than baseline? If yes -> a breadth gate could
     stand down condors before vol expansions.
  2. DIRECTION: when breadth is LOW, is SPY forward return WORSE than
     baseline (deterioration = weakness, the narrative's claim)?
  3. DIP-BUY (capitulation): when breadth is WASHED OUT (very low %), is
     forward return actually BETTER than baseline? That would be consistent
     with the proven buy-the-dip thesis — breadth washout = better entries,
     the OPPOSITE of the "low breadth = avoid" story.

If (3) beats (2), the honest read is "breadth is a dip-timing tool, not a
risk-off trigger." Research only — no source/threshold changes here.

Run: python -m backtests.sector_breadth_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

# Classic SPDR sectors (XLC excluded from the live tracker but on disk from
# 2018; we include every sector we have data for and let the denominator adapt).
SECTOR_TICKERS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
                  "XLI", "XLB", "XLU", "XLRE", "XLC")


def pct_above_ma(panel: dict[str, pd.Series], window: int) -> pd.Series:
    """% of sectors trading above their own `window`-day MA, per day.

    The denominator on each date counts only sectors that have a valid MA
    (i.e. enough history) — so a not-yet-listed sector never drags the
    reading toward 0. Returns a float Series in [0, 100]; NaN on dates
    where no sector has a valid MA.
    """
    df = pd.DataFrame(panel).sort_index()
    ma = df.rolling(window).mean()
    valid = ma.notna()
    above = (df > ma) & valid
    n_valid = valid.sum(axis=1)
    n_above = above.sum(axis=1)
    pct = n_above.where(n_valid > 0) / n_valid.where(n_valid > 0) * 100.0
    return pct.astype(float)


def conditional_forward(spy_df: pd.DataFrame, dates, horizon: int = 5) -> dict:
    """SPY forward `horizon`-day return + realized vol for a set of signal
    dates, against the unconditional baseline. Same shape as the vol-crush
    study so results read consistently across the research suite."""
    close = spy_df["close"].astype(float)
    daily = close.pct_change() * 100
    pos = {d: i for i, d in enumerate(close.index)}
    fwd_r, fwd_v = [], []
    for d in dates:
        i = pos.get(pd.Timestamp(d))
        if i is None or i + horizon >= len(close):
            continue
        fwd_r.append((close.iloc[i + horizon] / close.iloc[i] - 1) * 100)
        fwd_v.append(float(daily.iloc[i + 1: i + 1 + horizon].std()) if horizon > 1 else 0.0)
    n = len(fwd_r)
    base = (close.shift(-horizon) / close - 1) * 100
    return {
        "n":            n,
        "fwd_mean":     round(sum(fwd_r) / n, 4) if n else 0.0,
        "fwd_pct_up":   round(sum(1 for x in fwd_r if x > 0) / n * 100, 1) if n else 0.0,
        "baseline_fwd": round(float(base.dropna().mean()), 4),
        "fwd_vol":      round(sum(fwd_v) / n, 4) if n else 0.0,
        "baseline_vol": round(float(daily.rolling(horizon).std().dropna().mean()), 4),
    }


# ── data loading (real CSVs; not exercised by unit tests) ────────────────
def _load_close(ticker: str) -> pd.Series | None:
    path = os.path.join(os.path.dirname(__file__), f"{ticker.lower()}_history.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    return df["close"].astype(float)


def load_sector_panel() -> dict[str, pd.Series]:
    panel = {}
    for t in SECTOR_TICKERS:
        s = _load_close(t)
        if s is not None:
            panel[t] = s
    return panel


def main():
    from backtests.dipbuy_signal_study import load_spy
    spy = load_spy()
    panel = load_sector_panel()
    # align sector panel to SPY trading days
    panel = {t: s.reindex(spy.index.union(s.index)).ffill().reindex(spy.index)
             for t, s in panel.items()}

    b50 = pct_above_ma(panel, 50)
    b200 = pct_above_ma(panel, 200)

    print(f"Sector-breadth-as-risk-signal study — SPY {spy.index.min().date()}..{spy.index.max().date()}")
    print(f"sectors loaded: {len(panel)} ({', '.join(panel)})\n")

    # quantile cut-points of the 50d-breadth distribution
    q = b50.dropna()
    lo, mid, washout = q.quantile(0.25), q.quantile(0.50), q.quantile(0.10)
    print(f"50d-breadth distribution: median {q.median():.0f}%  p25 {lo:.0f}%  p10(washout) {washout:.0f}%\n")

    # falling-breadth (deterioration): breadth dropped >=15pts over 10d AND now < median
    falling = (b50 < (b50.shift(10) - 15)) & (b50 < q.median())

    bands = {
        f"LOW   breadth (<p25 = {lo:.0f}%)":        b50 < lo,
        f"WASHOUT (<p10 = {washout:.0f}%)":         b50 < washout,
        "FALLING (-15pts/10d & <median)":           falling,
        f"HIGH  breadth (>median {q.median():.0f}%)": b50 > q.median(),
    }

    print(f"{'condition':<34} {'h':>3} {'n':>4} {'fwd%':>8} {'base%':>7} {'verdict':<12} {'fvol':>6} {'bvol':>6} {'%up':>5}")
    for label, mask in bands.items():
        dates = list(spy.index[mask.reindex(spy.index).fillna(False).values])
        for h in (5, 10):
            r = conditional_forward(spy, dates, horizon=h)
            if not r["n"]:
                continue
            edge = r["fwd_mean"] - r["baseline_fwd"]
            verdict = "BETTER" if edge > 0.05 else ("WORSE" if edge < -0.05 else "≈base")
            vol = "vol↑" if r["fwd_vol"] > r["baseline_vol"] else "vol↓"
            print(f"{label:<34} {h:>3} {r['n']:>4} {r['fwd_mean']:>7.3f}% {r['baseline_fwd']:>6.3f}% "
                  f"{verdict:<6} {vol:<5} {r['fwd_vol']:>6.3f} {r['baseline_vol']:>6.3f} {r['fwd_pct_up']:>4.0f}%")
        print()


if __name__ == "__main__":
    main()
