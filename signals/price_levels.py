"""
signals/price_levels.py -- Support / resistance derived from price action.

Three pure-function helpers for the /levels page:

    recent_swing_levels(df, lookback=50)
        Last N-day high and low + the local pivot points (5-bar swings).
        Returns {high_N, low_N, swing_highs: [(date, price)], swing_lows: ...}

    moving_average_levels(df)
        Current MA20 / MA50 / MA200 values from the last bar.

    distance_pct(price, level)
        Convenience: "+1.42" or "-0.87" % from level.

These don't touch Polygon — caller supplies a daily-bar DataFrame with
'close' + 'high' + 'low' columns and a DatetimeIndex (or date index).
"""

from __future__ import annotations

import pandas as pd


SWING_WINDOW = 5   # bars on each side for local pivot detection


def recent_swing_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Returns {
      "high_N":       float | None,   # max(high) over last lookback bars
      "low_N":        float | None,
      "high_date":    "YYYY-MM-DD",
      "low_date":     "YYYY-MM-DD",
      "swing_highs":  [(date_str, price), ...],  # local pivot highs
      "swing_lows":   [(date_str, price), ...],
    }
    Local pivots use SWING_WINDOW bars on each side — a bar is a pivot
    high if its high is the max of the [i-W .. i+W] window. Same for lows.
    """
    if df is None or len(df) < 2:
        return _empty()

    cols_lower = {c.lower(): c for c in df.columns}
    if "high" not in cols_lower or "low" not in cols_lower:
        return _empty()
    high_col = cols_lower["high"]
    low_col  = cols_lower["low"]

    recent = df.tail(lookback)
    high_n  = float(recent[high_col].max())
    low_n   = float(recent[low_col].min())
    high_dt = _idx_to_str(recent[high_col].idxmax())
    low_dt  = _idx_to_str(recent[low_col].idxmin())

    swing_highs = _find_pivots(df, high_col, SWING_WINDOW, kind="high")
    swing_lows  = _find_pivots(df, low_col,  SWING_WINDOW, kind="low")

    # Keep only pivots that landed inside the lookback window
    cutoff = recent.index[0]
    swing_highs = [(d, p) for d, p in swing_highs if d >= _idx_to_str(cutoff)]
    swing_lows  = [(d, p) for d, p in swing_lows  if d >= _idx_to_str(cutoff)]

    return {
        "high_N":      high_n,
        "low_N":       low_n,
        "high_date":   high_dt,
        "low_date":    low_dt,
        "swing_highs": swing_highs,
        "swing_lows":  swing_lows,
        "lookback":    lookback,
    }


def moving_average_levels(df: pd.DataFrame) -> dict:
    """Current MA20 / MA50 / MA200 from the most recent close, or None each
    if not enough history."""
    if df is None or len(df) == 0 or "close" not in {c.lower() for c in df.columns}:
        return {"ma20": None, "ma50": None, "ma200": None, "close": None}
    close_col = next(c for c in df.columns if c.lower() == "close")
    closes = df[close_col].dropna()
    last   = float(closes.iloc[-1]) if len(closes) else None

    def _ma(window: int):
        if len(closes) < window:
            return None
        return round(float(closes.iloc[-window:].mean()), 2)

    return {
        "ma20":  _ma(20),
        "ma50":  _ma(50),
        "ma200": _ma(200),
        "close": last,
    }


def distance_pct(price: float | None, level: float | None) -> float | None:
    """% distance from price to level (positive = above)."""
    if price is None or level is None or level == 0:
        return None
    return round((price - level) / level * 100, 2)


# ── internals ──────────────────────────────────────

def _find_pivots(df: pd.DataFrame, col: str, w: int, kind: str) -> list[tuple]:
    """Generic n-bar pivot finder. Returns [(date_str, price), ...] sorted oldest→newest."""
    if len(df) < 2 * w + 1:
        return []
    vals = df[col].values
    out  = []
    for i in range(w, len(df) - w):
        window = vals[i - w : i + w + 1]
        if kind == "high" and vals[i] == window.max() and (window == vals[i]).sum() == 1:
            out.append((_idx_to_str(df.index[i]), float(vals[i])))
        elif kind == "low" and vals[i] == window.min() and (window == vals[i]).sum() == 1:
            out.append((_idx_to_str(df.index[i]), float(vals[i])))
    return out


def _idx_to_str(idx) -> str:
    if hasattr(idx, "isoformat"):
        return idx.isoformat()[:10]
    return str(idx)[:10]


def _empty() -> dict:
    return {
        "high_N":      None, "low_N":      None,
        "high_date":   None, "low_date":   None,
        "swing_highs": [],   "swing_lows": [],
        "lookback":    0,
    }
