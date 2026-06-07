"""backtests/dipbuy_signal_study.py -- Phase 1 dip-buy signal event-study.

Underlying-only (NO options). Measures whether a dip trigger predicts a
forward bounce, out-of-sample (per-calendar-year consistency). Gates the
later option-priced Phase 2. Research only; touches no live path.

Spec:  docs/superpowers/specs/2026-06-07-dipbuy-directional-study-design.md
Plan:  docs/superpowers/plans/2026-06-07-dipbuy-directional-study.md
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

import config


# ── Indicators + trigger predicates ─────────────────────────────────────────

def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI as a full series aligned to `close`."""
    delta = close.diff()
    gain  = delta.clip(lower=0.0)
    loss  = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)   # neutral before warmup / when no losses


def oversold_triggers(rsi: pd.Series, threshold: float = 30.0) -> pd.Series:
    """Fresh cross BELOW threshold: today < t, yesterday >= t. Dedups clusters
    so overlapping forward windows don't autocorrelate the sample."""
    below = rsi < threshold
    prev_not_below = ~(rsi.shift(1) < threshold)
    return (below & prev_not_below).fillna(False)


def pullback_triggers(close: pd.Series, ma20: pd.Series, ma200: pd.Series) -> pd.Series:
    """Uptrend intact (close>ma200) AND fresh dip below the 20MA
    (close<ma20 today, close>=ma20 yesterday)."""
    uptrend    = close > ma200
    below20    = close < ma20
    prev_above = ~(close.shift(1) < ma20.shift(1))
    return (uptrend & below20 & prev_above).fillna(False)


# ── Forward returns + edge vs baseline ──────────────────────────────────────

def forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    """Close-to-close % return `horizon` trading days ahead; NaN where unavailable."""
    fwd = close.shift(-horizon)
    return (fwd - close) / close * 100.0


def edge_vs_baseline(fwd: pd.Series, trig: pd.Series) -> dict:
    """Conditional (trigger-day) vs unconditional forward-return stats. Both
    restricted to days where the forward return is defined (not NaN)."""
    valid = fwd.notna()
    base  = fwd[valid]
    cond  = fwd[valid & trig.reindex(fwd.index, fill_value=False)]
    n = int(len(cond))
    cond_mean = float(cond.mean()) if n else 0.0
    base_mean = float(base.mean()) if len(base) else 0.0
    return {
        "n":             n,
        "cond_mean":     round(cond_mean, 4),
        "cond_median":   round(float(cond.median()), 4) if n else 0.0,
        "pct_positive":  round(float((cond > 0).mean()) * 100, 1) if n else 0.0,
        "baseline_mean": round(base_mean, 4),
        "edge":          round(cond_mean - base_mean, 4),
    }


# ── Per-year consistency + arm verdict ──────────────────────────────────────

def per_year_edges(fwd: pd.Series, trig: pd.Series) -> dict:
    """Edge (cond−baseline) per calendar year. Baseline is that year's
    unconditional mean. Returns {year: {n, edge}} for every year that has data;
    `n` is the trigger count so the verdict can weight by trigger-years."""
    valid  = fwd.notna()
    fwd_v  = fwd[valid]
    trig_v = trig.reindex(fwd.index, fill_value=False)[valid]
    out: dict[int, dict] = {}
    for year, mask in fwd_v.groupby(fwd_v.index.year).groups.items():
        yfwd  = fwd_v.loc[mask]
        ytrig = trig_v.loc[mask]
        cond  = yfwd[ytrig]
        base_mean = float(yfwd.mean()) if len(yfwd) else 0.0
        n = int(len(cond))
        out[int(year)] = {
            "n":    n,
            "edge": round((float(cond.mean()) - base_mean), 4) if n else 0.0,
        }
    return out


def arm_verdict(pooled_edge: float, pooled_cond_mean: float, per_year: dict,
                total_n: int, half_means: tuple[float, float]) -> dict:
    """Rare-signal-aware verdict. An arm SURVIVES iff ALL hold:
      - total triggers >= DIPBUY_MIN_TOTAL_TRIGGERS (else under-powered/inconclusive)
      - pooled conditional mean forward return > 0
      - pooled edge vs baseline >= DIPBUY_MIN_EDGE_PCT
      - positive edge in >= DIPBUY_MIN_OOS_YEAR_FRAC of TRIGGER-years (n>=1)
      - BOTH chronological halves of the conditional series are positive
        (a one-era fluke fails even if the pooled number looks great).

    Trigger-years (n>=1) replace the original per-year ">=5 triggers" gate,
    which spuriously failed rare-but-real signals; the half-split + total-N
    floor guard against rarity hiding a fluke."""
    trigger_years = {y: d for y, d in per_year.items() if d["n"] >= 1}
    pos  = sum(1 for d in trigger_years.values() if d["edge"] > 0)
    frac = (pos / len(trigger_years)) if trigger_years else 0.0
    half_ok = min(half_means) > 0
    survives = bool(
        total_n >= config.DIPBUY_MIN_TOTAL_TRIGGERS
        and pooled_cond_mean > 0
        and pooled_edge >= config.DIPBUY_MIN_EDGE_PCT
        and frac >= config.DIPBUY_MIN_OOS_YEAR_FRAC
        and half_ok
    )
    return {
        "survives":      survives,
        "pooled_edge":   round(pooled_edge, 4),
        "pos_year_frac": round(frac, 3),
        "trigger_years": len(trigger_years),
        "total_n":       int(total_n),
        "half_means":    (round(half_means[0], 3), round(half_means[1], 3)),
        "half_ok":       half_ok,
    }


# ── Loader + arm runner + report ────────────────────────────────────────────

_YF_CSV = os.path.join(os.path.dirname(__file__), "spy_history_yf.csv")


def load_spy(path: str = _YF_CSV, start: str = "2010-01-01") -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    df = df[df.index >= pd.Timestamp(start)]
    if len(df) < 250:
        raise ValueError(f"insufficient SPY history in {path}: {len(df)} rows")
    return df


def _triggers_for(df: pd.DataFrame, arm: str) -> pd.Series:
    close = df["close"].astype(float)
    if arm == "oversold":
        return oversold_triggers(rsi_series(close, 14), 30.0)
    if arm == "pullback":
        ma20  = close.rolling(20).mean()
        ma200 = close.rolling(200).mean()
        return pullback_triggers(close, ma20, ma200)
    raise ValueError(f"unknown arm {arm!r}")


def run_arm(df: pd.DataFrame, arm: str) -> dict:
    close = df["close"].astype(float)
    trig  = _triggers_for(df, arm)
    by_h, verdicts = {}, {}
    for h in config.DIPBUY_FWD_HORIZONS:
        fwd   = forward_returns(close, h)
        stats = edge_vs_baseline(fwd, trig)
        pye   = per_year_edges(fwd, trig)
        # chronological half-split of the conditional (trigger-day) returns
        cond  = fwd[fwd.notna() & trig.reindex(fwd.index, fill_value=False)]
        half  = len(cond) // 2
        half_means = ((float(cond.iloc[:half].mean()) if half else 0.0),
                      (float(cond.iloc[half:].mean()) if len(cond) - half else 0.0))
        verdicts[h] = arm_verdict(stats["edge"], stats["cond_mean"], pye,
                                  total_n=stats["n"], half_means=half_means)
        by_h[h] = {**stats, "per_year": pye, **verdicts[h]}
    survived = {h: v for h, v in verdicts.items() if v["survives"]}
    return {
        "arm": arm,
        "by_horizon": by_h,
        "verdict": {"survives": bool(survived),
                    "horizons_passed": sorted(survived.keys())},
    }


def main():
    df = load_spy()
    print(f"Dip-buy signal study — SPY {df.index.min().date()}..{df.index.max().date()} "
          f"({len(df)} bars)\n")
    report = {}
    for arm in ("oversold", "pullback"):
        res = report[arm] = run_arm(df, arm)
        print(f"=== ARM: {arm} ===")
        print(f"{'h':>3} {'n':>5} {'cond%':>8} {'base%':>8} {'edge%':>8} "
              f"{'pos_yr':>7} {'survive':>8}")
        for h, s in res["by_horizon"].items():
            print(f"{h:>3} {s['n']:>5} {s['cond_mean']:>8.3f} {s['baseline_mean']:>8.3f} "
                  f"{s['edge']:>8.3f} {s['pos_year_frac']:>7.2f} "
                  f"{str(s['survives']):>8}")
        print(f"  → arm survives: {res['verdict']['survives']} "
              f"(horizons {res['verdict']['horizons_passed']})\n")
    return report


if __name__ == "__main__":
    main()
