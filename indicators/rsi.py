"""
indicators/rsi.py — RSI + Option B Divergence Detection
Uses confirmed pivot points on BOTH price and RSI before flagging divergence.

Option B rules:
    1. Identify a confirmed pivot high/low on price (requires RSI_DIVERGENCE_LOOKBACK candles either side)
    2. Identify a confirmed pivot high/low on RSI at the same location
    3. Compare two consecutive pivots — if they disagree in direction = divergence

Usage:
    from indicators.rsi import RSIAnalysis
    rsi = RSIAnalysis(df)
    result = rsi.analyze()
"""

import pandas as pd
import numpy as np
from loguru import logger
import config


class RSIAnalysis:
    """
    Calculates RSI and detects confirmed divergence using pivot point method.

    Scoring contribution (max 12 pts — Setup Layer):
        Confirmed bullish divergence    → +12 pts
        Confirmed bearish divergence    → +12 pts
        No divergence                   → +0  pts

    Note: RSI overbought/oversold levels are NOT used for scoring.
    Divergence only.
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.period = config.RSI_PERIOD
        self.pivot_lookback = config.RSI_DIVERGENCE_LOOKBACK
        self.result = {}

    def analyze(self) -> dict:
        """
        Run RSI + divergence analysis.

        Returns dict with:
            rsi_current           — current RSI value
            rsi_prev              — previous bar RSI
            rsi_trend             — "rising", "falling", "flat"
            bullish_divergence    — True if confirmed bullish divergence
            bearish_divergence    — True if confirmed bearish divergence
            divergence_strength   — "strong", "moderate", or None
            score                 — setup layer contribution (0-12)
            score_breakdown       — explanation
        """
        min_bars = self.period + (self.pivot_lookback * 2) + 5
        if len(self.df) < min_bars:
            logger.warning(f"Not enough data for RSI divergence. Need {min_bars} bars.")
            return self._empty_result()

        self._calculate_rsi()
        self._find_pivots()
        self._detect_divergence()
        self._calculate_score()

        logger.debug(
            f"RSI analysis complete — RSI: {self.result['rsi_current']:.1f}, "
            f"Bull div: {self.result['bullish_divergence']}, "
            f"Bear div: {self.result['bearish_divergence']}, "
            f"score: {self.result['score']}/12"
        )
        return self.result

    def _calculate_rsi(self):
        close = self.df["close"]
        delta = close.diff()

        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        # Wilder smoothing (standard RSI method)
        avg_gain = gain.ewm(com=self.period - 1, min_periods=self.period).mean()
        avg_loss = loss.ewm(com=self.period - 1, min_periods=self.period).mean()

        rs  = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.fillna(50)

        self.df["rsi"] = rsi

        self.result["rsi_current"] = round(float(rsi.iloc[-1]), 2)
        self.result["rsi_prev"]    = round(float(rsi.iloc[-2]), 2)

        diff = self.result["rsi_current"] - self.result["rsi_prev"]
        if diff > 0.5:
            self.result["rsi_trend"] = "rising"
        elif diff < -0.5:
            self.result["rsi_trend"] = "falling"
        else:
            self.result["rsi_trend"] = "flat"

    def _find_pivots(self):
        """
        Option B — find confirmed pivot highs and lows on BOTH price and RSI.
        A pivot high is confirmed when it has N bars of lower highs on each side.
        A pivot low  is confirmed when it has N bars of higher lows on each side.
        """
        n = self.pivot_lookback

        price_pivot_highs = []
        price_pivot_lows  = []
        rsi_pivot_highs   = []
        rsi_pivot_lows    = []

        # Only look at recent portion for performance
        lookback_bars = min(len(self.df), 60)
        df_slice = self.df.tail(lookback_bars).reset_index(drop=True)

        for i in range(n, len(df_slice) - n):
            # Price pivot high
            if all(df_slice["high"].iloc[i] > df_slice["high"].iloc[i - j] for j in range(1, n + 1)) and \
               all(df_slice["high"].iloc[i] > df_slice["high"].iloc[i + j] for j in range(1, n + 1)):
                price_pivot_highs.append((i, float(df_slice["close"].iloc[i])))
                rsi_pivot_highs.append((i, float(df_slice["rsi"].iloc[i])))

            # Price pivot low
            if all(df_slice["low"].iloc[i] < df_slice["low"].iloc[i - j] for j in range(1, n + 1)) and \
               all(df_slice["low"].iloc[i] < df_slice["low"].iloc[i + j] for j in range(1, n + 1)):
                price_pivot_lows.append((i, float(df_slice["close"].iloc[i])))
                rsi_pivot_lows.append((i, float(df_slice["rsi"].iloc[i])))

        self._price_pivot_highs = price_pivot_highs
        self._price_pivot_lows  = price_pivot_lows
        self._rsi_pivot_highs   = rsi_pivot_highs
        self._rsi_pivot_lows    = rsi_pivot_lows

    def _detect_divergence(self):
        """
        Compare last two confirmed pivots on price vs RSI.

        Bullish divergence: price makes lower low, RSI makes higher low
        Bearish divergence: price makes higher high, RSI makes lower high
        """
        self.result["bullish_divergence"]  = False
        self.result["bearish_divergence"]  = False
        self.result["divergence_strength"] = None

        # Bullish divergence — check lows
        if len(self._price_pivot_lows) >= 2 and len(self._rsi_pivot_lows) >= 2:
            p_low1, p_val1 = self._price_pivot_lows[-2]
            p_low2, p_val2 = self._price_pivot_lows[-1]
            r_low1, r_val1 = self._rsi_pivot_lows[-2]
            r_low2, r_val2 = self._rsi_pivot_lows[-1]

            # Price lower low + RSI higher low = bullish divergence
            if p_val2 < p_val1 and r_val2 > r_val1:
                self.result["bullish_divergence"] = True
                price_diff = abs(p_val2 - p_val1) / p_val1
                rsi_diff   = abs(r_val2 - r_val1)
                self.result["divergence_strength"] = "strong" if rsi_diff > 5 and price_diff > 0.02 else "moderate"

        # Bearish divergence — check highs
        if len(self._price_pivot_highs) >= 2 and len(self._rsi_pivot_highs) >= 2:
            p_hi1, p_val1 = self._price_pivot_highs[-2]
            p_hi2, p_val2 = self._price_pivot_highs[-1]
            r_hi1, r_val1 = self._rsi_pivot_highs[-2]
            r_hi2, r_val2 = self._rsi_pivot_highs[-1]

            # Price higher high + RSI lower high = bearish divergence
            if p_val2 > p_val1 and r_val2 < r_val1:
                self.result["bearish_divergence"] = True
                price_diff = abs(p_val2 - p_val1) / p_val1
                rsi_diff   = abs(r_val2 - r_val1)
                self.result["divergence_strength"] = "strong" if rsi_diff > 5 and price_diff > 0.02 else "moderate"

    def _calculate_score(self):
        score = 0
        breakdown = {}

        if self.result["bullish_divergence"] or self.result["bearish_divergence"]:
            strength = self.result["divergence_strength"]
            points = 12 if strength == "strong" else 8
            score += points
            div_type = "bullish" if self.result["bullish_divergence"] else "bearish"
            breakdown["rsi_divergence"] = {
                "points": points,
                "reason": f"Confirmed {strength} {div_type} RSI divergence (pivot method)"
            }
        else:
            breakdown["rsi_divergence"] = {
                "points": 0,
                "reason": "No confirmed RSI divergence on pivot points"
            }

        self.result["score"] = score
        self.result["score_breakdown"] = breakdown

    def _empty_result(self) -> dict:
        return {
            "rsi_current": None, "rsi_prev": None, "rsi_trend": "unknown",
            "bullish_divergence": False, "bearish_divergence": False,
            "divergence_strength": None,
            "score": 0, "score_breakdown": {}
        }