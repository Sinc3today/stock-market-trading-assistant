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
