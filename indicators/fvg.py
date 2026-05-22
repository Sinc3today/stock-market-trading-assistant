"""
indicators/fvg.py -- Daily Fair Value Gap (FVG) detection + features.

An FVG is a 3-candle imbalance: candle i-1's surge leaves an untraded gap
between candle i-2 and candle i that price tends to revisit ("fill"). We only
keep UNFILLED gaps (no later bar's range covers them) and expose three features
relative to a spot price for the meta-labeler to learn from.
"""

from __future__ import annotations

import pandas as pd


def detect_fvgs(df: pd.DataFrame) -> list[dict]:
    """Return unfilled FVGs as [{type, top, bottom, idx}], scanning the frame."""
    highs = df["high"].tolist()
    lows  = df["low"].tolist()
    gaps  = []
    for i in range(2, len(df)):
        # Bullish: candle i low above candle i-2 high -> gap (i-2 high, i low)
        if lows[i] > highs[i - 2]:
            gaps.append({"type": "bull", "bottom": highs[i - 2], "top": lows[i], "idx": i})
        # Bearish: candle i high below candle i-2 low -> gap (i high, i-2 low)
        elif highs[i] < lows[i - 2]:
            gaps.append({"type": "bear", "bottom": highs[i], "top": lows[i - 2], "idx": i})

    # Drop gaps later filled: any subsequent bar whose range overlaps the zone.
    unfilled = []
    for g in gaps:
        filled = False
        for j in range(g["idx"] + 1, len(df)):
            if lows[j] <= g["top"] and highs[j] >= g["bottom"]:
                filled = True
                break
        if not filled:
            unfilled.append(g)
    return unfilled


def fvg_features(df: pd.DataFrame, spot: float) -> dict:
    """inside_fvg / dist_to_nearest_fvg(%) / fvg_size(%) for the unfilled gaps."""
    empty = {"inside_fvg": 0, "dist_to_nearest_fvg": 0.0, "fvg_size": 0.0}
    if spot <= 0:
        return empty
    gaps = detect_fvgs(df)
    if not gaps:
        return empty

    inside = next((g for g in gaps if g["bottom"] <= spot <= g["top"]), None)
    if inside is not None:
        return {
            "inside_fvg": 1,
            "dist_to_nearest_fvg": 0.0,
            "fvg_size": round((inside["top"] - inside["bottom"]) / spot * 100, 3),
        }

    def edge_dist(g):
        return min(abs(spot - g["top"]), abs(spot - g["bottom"]))

    nearest = min(gaps, key=edge_dist)
    return {
        "inside_fvg": 0,
        "dist_to_nearest_fvg": round(edge_dist(nearest) / spot * 100, 3),
        "fvg_size": round((nearest["top"] - nearest["bottom"]) / spot * 100, 3),
    }
