"""
indicators/moving_averages.py — Moving Average calculations
Handles MA 20/50/200, stack detection, and trend scoring.

Usage:
    from indicators.moving_averages import MovingAverages
    ma = MovingAverages(df)
    result = ma.analyze()
"""

import pandas as pd
import numpy as np
from loguru import logger
import config


class MovingAverages:
    """
    Calculates MA 20/50/200 and detects trend alignment.

    Scoring contribution (max 35 pts — Trend Layer):
        MA stack fully aligned          → +15 pts
        Price position vs key MA        → +10 pts
        Higher highs / higher lows      → +10 pts
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.result = {}

    def analyze(self) -> dict:
        """
        Run full moving average analysis.

        Returns dict with:
            ma20, ma50, ma200         — current MA values
            stack_bullish             — True if 20 > 50 > 200
            stack_bearish             — True if 20 < 50 < 200
            price_vs_200              — "above" or "below"
            price_vs_50               — "above" or "below"
            trend_direction           — "bullish", "bearish", "neutral"
            higher_highs_lows         — True if structure is bullish
            lower_highs_lows          — True if structure is bearish
            score                     — trend layer score (0-35)
            score_breakdown           — dict explaining each point
        """
        if len(self.df) < config.MA_LONG:
            logger.warning(f"Not enough data for MA{config.MA_LONG}. Got {len(self.df)} bars, need {config.MA_LONG}.")
            return self._empty_result()

        self._calculate_mas()
        self._detect_stack()
        self._detect_price_position()
        self._detect_swing_structure()
        self._calculate_score()

        logger.debug(f"MA analysis complete — score: {self.result['score']}/35")
        return self.result

    def _calculate_mas(self):
        self.df["ma20"]  = self.df["close"].rolling(window=config.MA_SHORT).mean()
        self.df["ma50"]  = self.df["close"].rolling(window=config.MA_MID).mean()
        self.df["ma200"] = self.df["close"].rolling(window=config.MA_LONG).mean()

        latest = self.df.iloc[-1]
        self.result["ma20"]  = round(float(latest["ma20"]),  2)
        self.result["ma50"]  = round(float(latest["ma50"]),  2)
        self.result["ma200"] = round(float(latest["ma200"]), 2)
        self.result["close"] = round(float(latest["close"]), 2)

    def _detect_stack(self):
        ma20  = self.result["ma20"]
        ma50  = self.result["ma50"]
        ma200 = self.result["ma200"]
        self.result["stack_bullish"] = ma20 > ma50 > ma200
        self.result["stack_bearish"] = ma20 < ma50 < ma200
        self.result["stack_neutral"] = not (self.result["stack_bullish"] or self.result["stack_bearish"])

    def _detect_price_position(self):
        close = self.result["close"]
        self.result["price_vs_200"] = "above" if close > self.result["ma200"] else "below"
        self.result["price_vs_50"]  = "above" if close > self.result["ma50"]  else "below"

        if self.result["stack_bullish"] and self.result["price_vs_200"] == "above":
            self.result["trend_direction"] = "bullish"
        elif self.result["stack_bearish"] and self.result["price_vs_200"] == "below":
            self.result["trend_direction"] = "bearish"
        else:
            self.result["trend_direction"] = "neutral"

    def _detect_swing_structure(self):
        lookback = 10
        if len(self.df) < lookback:
            self.result["higher_highs_lows"] = False
            self.result["lower_highs_lows"]  = False
            return

        recent = self.df.tail(lookback)
        mid = lookback // 2
        first_half  = recent.iloc[:mid]
        second_half = recent.iloc[mid:]

        first_high  = float(first_half["high"].max())
        second_high = float(second_half["high"].max())
        first_low   = float(first_half["low"].min())
        second_low  = float(second_half["low"].min())

        self.result["higher_highs_lows"] = (second_high > first_high and second_low > first_low)
        self.result["lower_highs_lows"]  = (second_high < first_high and second_low < first_low)

    def _calculate_score(self):
        score = 0
        breakdown = {}

        if self.result["stack_bullish"] or self.result["stack_bearish"]:
            score += 15
            breakdown["ma_stack"] = {"points": 15, "reason": "MA 20/50/200 fully aligned"}
        else:
            breakdown["ma_stack"] = {"points": 0, "reason": "MA stack not aligned"}

        trend = self.result["trend_direction"]
        if (trend == "bullish" and self.result["price_vs_200"] == "above") or \
           (trend == "bearish" and self.result["price_vs_200"] == "below"):
            score += 10
            breakdown["price_position"] = {"points": 10, "reason": f"Price {self.result['price_vs_200']} MA200"}
        else:
            breakdown["price_position"] = {"points": 0, "reason": "Price position weak vs MA200"}

        if self.result["higher_highs_lows"] or self.result["lower_highs_lows"]:
            score += 10
            structure = "HH/HL" if self.result["higher_highs_lows"] else "LH/LL"
            breakdown["swing_structure"] = {"points": 10, "reason": f"Clean swing structure ({structure})"}
        else:
            breakdown["swing_structure"] = {"points": 0, "reason": "No clean swing structure"}

        self.result["score"] = score
        self.result["score_breakdown"] = breakdown

    def _empty_result(self) -> dict:
        return {
            "ma20": None, "ma50": None, "ma200": None, "close": None,
            "stack_bullish": False, "stack_bearish": False, "stack_neutral": True,
            "price_vs_200": "unknown", "price_vs_50": "unknown",
            "trend_direction": "neutral",
            "higher_highs_lows": False, "lower_highs_lows": False,
            "score": 0, "score_breakdown": {}
        }