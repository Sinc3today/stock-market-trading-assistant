"""
indicators/cvd.py — Cumulative Volume Delta
Tracks whether buyers or sellers are driving volume.

CVD rises  → more buying pressure  (bullish)
CVD falls  → more selling pressure (bearish)

Since we're using bar data (not tick data), we approximate
volume delta using the close position within the bar range.

Usage:
    from indicators.cvd import CVDAnalysis
    cvd = CVDAnalysis(df)
    result = cvd.analyze()
"""

import pandas as pd
import numpy as np
from loguru import logger


class CVDAnalysis:
    """
    Calculates approximated Cumulative Volume Delta from OHLCV bars.

    Bar-level approximation formula:
        delta = volume * ((close - low) - (high - close)) / (high - low)

    This gives positive delta when close is near the high (buyers won the bar)
    and negative delta when close is near the low (sellers won the bar).

    Scoring contribution (max 12 pts — Volume Layer):
        CVD direction matches price move    → +12 pts
        CVD direction neutral               → +4  pts
        CVD diverges from price move        → +0  pts
    """

    def __init__(self, df: pd.DataFrame, lookback: int = 20):
        self.df = df.copy()
        self.lookback = lookback
        self.result = {}

    def analyze(self) -> dict:
        """
        Run CVD analysis.

        Returns dict with:
            cvd_current       — current cumulative delta value
            cvd_slope         — direction of CVD over lookback ("rising", "falling", "flat")
            delta_last_bar    — delta on the most recent bar
            cvd_matches_price — True if CVD direction matches price direction
            score             — volume layer contribution (0-12)
            score_breakdown   — explanation
        """
        if len(self.df) < self.lookback + 1:
            logger.warning(f"Not enough data for CVD. Need {self.lookback + 1} bars.")
            return self._empty_result()

        self._calculate_cvd()
        self._assess_direction()
        self._calculate_score()

        logger.debug(f"CVD analysis complete — slope: {self.result['cvd_slope']}, score: {self.result['score']}/12")
        return self.result

    def _calculate_cvd(self):
        df = self.df.tail(self.lookback + 1).copy()

        # Calculate bar delta — approximation from OHLCV
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        vol   = df["volume"]

        bar_range = high - low
        # Avoid division by zero on doji candles
        bar_range = bar_range.replace(0, np.nan)

        delta = vol * ((close - low) - (high - close)) / bar_range
        delta = delta.fillna(0)

        df["delta"] = delta
        df["cvd"]   = delta.cumsum()

        self.result["cvd_current"]    = round(float(df["cvd"].iloc[-1]), 2)
        self.result["cvd_start"]      = round(float(df["cvd"].iloc[0]),  2)
        self.result["delta_last_bar"] = round(float(df["delta"].iloc[-1]), 2)
        self._cvd_series = df["cvd"]

    def _assess_direction(self):
        cvd_now   = self.result["cvd_current"]
        cvd_start = self.result["cvd_start"]
        diff      = cvd_now - cvd_start

        # CVD slope
        threshold = abs(cvd_start) * 0.02 if cvd_start != 0 else 1000
        if diff > threshold:
            self.result["cvd_slope"] = "rising"
        elif diff < -threshold:
            self.result["cvd_slope"] = "falling"
        else:
            self.result["cvd_slope"] = "flat"

        # Price direction over same lookback
        price_start = float(self.df.iloc[-self.lookback]["close"])
        price_end   = float(self.df.iloc[-1]["close"])
        price_up    = price_end > price_start

        # Does CVD confirm the price move?
        if price_up and self.result["cvd_slope"] == "rising":
            self.result["cvd_matches_price"] = True
            self.result["cvd_signal"] = "bullish_confirmed"
        elif not price_up and self.result["cvd_slope"] == "falling":
            self.result["cvd_matches_price"] = True
            self.result["cvd_signal"] = "bearish_confirmed"
        elif self.result["cvd_slope"] == "flat":
            self.result["cvd_matches_price"] = False
            self.result["cvd_signal"] = "neutral"
        else:
            self.result["cvd_matches_price"] = False
            self.result["cvd_signal"] = "divergence"  # Warning sign

    def _calculate_score(self):
        score = 0
        breakdown = {}
        signal = self.result["cvd_signal"]

        if signal in ("bullish_confirmed", "bearish_confirmed"):
            score += 12
            breakdown["cvd"] = {
                "points": 12,
                "reason": f"CVD confirms price move ({signal.replace('_', ' ')})"
            }
        elif signal == "neutral":
            score += 4
            breakdown["cvd"] = {
                "points": 4,
                "reason": "CVD flat — no strong conviction either way"
            }
        else:
            breakdown["cvd"] = {
                "points": 0,
                "reason": "CVD diverging from price — caution flag ⚠️"
            }

        self.result["score"] = score
        self.result["score_breakdown"] = breakdown

    def _empty_result(self) -> dict:
        return {
            "cvd_current": None, "cvd_start": None,
            "delta_last_bar": None, "cvd_slope": "unknown",
            "cvd_matches_price": False, "cvd_signal": "unknown",
            "score": 0, "score_breakdown": {}
        }