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

def per_year_edges(fwd: pd.Series, trig: pd.Series, min_triggers: int) -> dict:
    """Edge (cond−baseline) per calendar year. Baseline is that year's
    unconditional mean. Years with < min_triggers are still returned but
    flagged via 'n' so the verdict can exclude them."""
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


def arm_verdict(pooled_edge: float, pooled_cond_mean: float, per_year: dict) -> dict:
    """An arm SURVIVES iff pooled cond mean > 0 AND pooled edge >= the floor AND
    positive-edge in >= the required fraction of qualifying years (>= 3 of them)."""
    qualifying = {y: d for y, d in per_year.items()
                  if d["n"] >= config.DIPBUY_MIN_TRIGGERS_PER_WINDOW}
    pos  = sum(1 for d in qualifying.values() if d["edge"] > 0)
    frac = (pos / len(qualifying)) if qualifying else 0.0
    survives = bool(
        pooled_cond_mean > 0
        and pooled_edge >= config.DIPBUY_MIN_EDGE_PCT
        and frac >= config.DIPBUY_MIN_OOS_YEAR_FRAC
        and len(qualifying) >= 3
    )
    return {
        "survives":         survives,
        "pooled_edge":      round(pooled_edge, 4),
        "pos_year_frac":    round(frac, 3),
        "qualifying_years": len(qualifying),
    }
