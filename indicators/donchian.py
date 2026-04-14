"""
indicators/donchian.py — Donchian Channel calculations
Detects breakouts based on channel highs/lows.
Breakout must be a CLOSE above/below channel — not just a wick.

Usage:
    from indicators.donchian import DonchianChannels
    dc = DonchianChannels(df)
    result = dc.analyze()
"""

import pandas as pd
from loguru import logger
import config


class DonchianChannels:
    """
    Calculates Donchian Channels and detects confirmed breakouts.

    Scoring contribution (max 15 pts — Setup Layer):
        Confirmed close breakout        → +15 pts
        Price near channel boundary     → +7  pts (partial)
    """

    def __init__(self, df: pd.DataFrame, period: int = None):
        self.df = df.copy()
        self.period = period or config.DONCHIAN_PERIOD
        self.result = {}

    def analyze(self) -> dict:
        """
        Run Donchian channel analysis.

        Returns dict with:
            upper_band        — highest high over period
            lower_band        — lowest low over period
            middle_band       — midpoint of channel
            close             — current close price
            breakout_up       — True if close > upper band (bullish breakout)
            breakout_down     — True if close < lower band (bearish breakout)
            near_upper        — True if price within 1% of upper band
            near_lower        — True if price within 1% of lower band
            channel_width_pct — channel width as % of price (volatility proxy)
            score             — setup layer contribution (0-15)
            score_breakdown   — explanation
        """
        if len(self.df) < self.period + 1:
            logger.warning(f"Not enough data for Donchian({self.period}). Need {self.period + 1} bars.")
            return self._empty_result()

        self._calculate_channels()
        self._detect_breakout()
        self._calculate_score()

        logger.debug(f"Donchian analysis complete — score: {self.result['score']}/15")
        return self.result

    def _calculate_channels(self):
        # Use period bars EXCLUDING the current bar to avoid lookahead
        lookback = self.df.iloc[-(self.period + 1):-1]

        upper = float(lookback["high"].max())
        lower = float(lookback["low"].min())
        middle = round((upper + lower) / 2, 2)
        close  = float(self.df.iloc[-1]["close"])

        self.result["upper_band"]  = round(upper,  2)
        self.result["lower_band"]  = round(lower,  2)
        self.result["middle_band"] = middle
        self.result["close"]       = round(close,  2)
        self.result["channel_width_pct"] = round(((upper - lower) / close) * 100, 2)

    def _detect_breakout(self):
        close = self.result["close"]
        upper = self.result["upper_band"]
        lower = self.result["lower_band"]

        # Hard breakout — close must clear the channel (no wick-only breaks)
        self.result["breakout_up"]   = close > upper
        self.result["breakout_down"] = close < lower

        # Near boundary — within 1% of the channel edge
        proximity_pct = 0.01
        self.result["near_upper"] = (not self.result["breakout_up"]) and \
                                    (close >= upper * (1 - proximity_pct))
        self.result["near_lower"] = (not self.result["breakout_down"]) and \
                                    (close <= lower * (1 + proximity_pct))

    def _calculate_score(self) -> int:
        score = 0
        breakdown = {}

        if self.result["breakout_up"] or self.result["breakout_down"]:
            score += 15
            direction = "UP" if self.result["breakout_up"] else "DOWN"
            breakdown["donchian_breakout"] = {
                "points": 15,
                "reason": f"Confirmed close breakout {direction} through {self.period}-period channel"
            }
        elif self.result["near_upper"] or self.result["near_lower"]:
            score += 7
            side = "upper" if self.result["near_upper"] else "lower"
            breakdown["donchian_breakout"] = {
                "points": 7,
                "reason": f"Price testing {side} channel boundary (within 1%)"
            }
        else:
            breakdown["donchian_breakout"] = {
                "points": 0,
                "reason": "No breakout or channel test detected"
            }

        self.result["score"] = score
        self.result["score_breakdown"] = breakdown

    def _empty_result(self) -> dict:
        return {
            "upper_band": None, "lower_band": None,
            "middle_band": None, "close": None,
            "breakout_up": False, "breakout_down": False,
            "near_upper": False, "near_lower": False,
            "channel_width_pct": None,
            "score": 0, "score_breakdown": {}
        }