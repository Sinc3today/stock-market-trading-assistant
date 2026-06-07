"""
signals/feature_builder.py -- Single source of truth for meta-label features.

Called by BOTH the offline training path (learning/meta_dataset.py, fed from
SPYBacktest rows) and the live scoring path (signals/spy_daily_strategy.py, fed
from RegimeResult). Identical inputs MUST yield an identical vector -- that
parity is the reason this lives in one function.

`metrics` is the dict shape shared by RegimeResult.metrics and SPYBacktest rows:
keys adx, vix, ivr, ma200_dist_%, spy_close.
"""

from __future__ import annotations

import pandas as pd

from indicators.fvg import fvg_features

# Baseline feature order (model input order). FVG features are appended only
# when include_fvg=True and are NOT part of the baseline vector.
FEATURE_ORDER = [
    "adx", "vix", "ivr", "ma200_dist_pct",
    "regime_trending_up", "regime_trending_down", "regime_choppy_low_vol",
]
FVG_ORDER = ["inside_fvg", "dist_to_nearest_fvg", "fvg_size"]


def build_features(regime: str, metrics: dict,
                   spy_df: pd.DataFrame | None = None,
                   include_fvg: bool = False) -> dict:
    """Build the named feature dict for one day."""
    f = {
        "adx":            float(metrics.get("adx", 0.0)),
        "vix":            float(metrics.get("vix", 0.0)),
        "ivr":            float(metrics.get("ivr", 0.0)),
        "ma200_dist_pct": float(metrics.get("ma200_dist_%", 0.0)),
        "regime_trending_up":    1 if regime == "trending_up_calm" else 0,
        "regime_trending_down":  1 if regime == "trending_down_calm" else 0,
        # transition-zone chop is a (half-size) condor regime — keep it under
        # the same feature it had before the label split, so meta features are
        # unchanged for the (currently disabled) meta-labeler.
        "regime_choppy_low_vol": 1 if regime in ("choppy_low_vol", "choppy_transition") else 0,
    }
    if include_fvg and spy_df is not None:
        f.update(fvg_features(spy_df, float(metrics.get("spy_close", 0.0))))
    return f


def to_vector(features: dict) -> list[float]:
    """Project a feature dict onto the fixed order (baseline + FVG if present)."""
    order = FEATURE_ORDER + (FVG_ORDER if "inside_fvg" in features else [])
    return [float(features[k]) for k in order]
