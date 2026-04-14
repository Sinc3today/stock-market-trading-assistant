"""
indicators/volume.py — Volume analysis
Detects volume spikes and calculates relative volume (RVOL).

Usage:
    from indicators.volume import VolumeAnalysis
    vol = VolumeAnalysis(df)
    result = vol.analyze()
"""

import pandas as pd
from loguru import logger
import config


class VolumeAnalysis:
    """
    Analyzes volume for conviction confirmation.

    Scoring contribution (max 12 pts — Volume Layer):
        Volume >= 1.5x average          → +12 pts
        Volume >= 1.2x average          → +6  pts (partial)
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.result = {}

    def analyze(self) -> dict:
        """
        Run volume analysis.

        Returns dict with:
            current_volume    — most recent bar volume
            avg_volume        — average volume over lookback period
            rvol              — relative volume (current / average)
            volume_spike      — True if rvol >= threshold
            volume_direction  — "up" or "down" based on close vs open
            score             — volume layer contribution (0-12)
            score_breakdown   — explanation
        """
        if len(self.df) < config.VOLUME_LOOKBACK + 1:
            logger.warning(f"Not enough data for volume analysis. Need {config.VOLUME_LOOKBACK + 1} bars.")
            return self._empty_result()

        self._calculate_volume()
        self._calculate_score()

        logger.debug(f"Volume analysis complete — RVOL: {self.result['rvol']:.2f}x, score: {self.result['score']}/12")
        return self.result

    def _calculate_volume(self):
        # Average volume excludes current bar
        lookback = self.df.iloc[-(config.VOLUME_LOOKBACK + 1):-1]
        avg_volume = float(lookback["volume"].mean())

        latest = self.df.iloc[-1]
        current_volume = float(latest["volume"])
        rvol = round(current_volume / avg_volume, 2) if avg_volume > 0 else 0

        self.result["current_volume"] = int(current_volume)
        self.result["avg_volume"]     = int(avg_volume)
        self.result["rvol"]           = rvol
        self.result["volume_spike"]   = rvol >= config.VOLUME_SPIKE_MULTIPLIER

        # Volume direction — did price close up or down on this volume?
        self.result["volume_direction"] = "up" if float(latest["close"]) >= float(latest["open"]) else "down"

    def _calculate_score(self):
        score = 0
        breakdown = {}
        rvol = self.result["rvol"]

        if rvol >= config.VOLUME_SPIKE_MULTIPLIER:        # >= 1.5x
            score += 12
            breakdown["volume_spike"] = {
                "points": 12,
                "reason": f"Strong volume spike: {rvol:.1f}x average"
            }
        elif rvol >= 1.2:                                  # >= 1.2x (partial)
            score += 6
            breakdown["volume_spike"] = {
                "points": 6,
                "reason": f"Moderate volume increase: {rvol:.1f}x average"
            }
        else:
            breakdown["volume_spike"] = {
                "points": 0,
                "reason": f"Weak volume: {rvol:.1f}x average (need {config.VOLUME_SPIKE_MULTIPLIER}x)"
            }

        self.result["score"] = score
        self.result["score_breakdown"] = breakdown

    def _empty_result(self) -> dict:
        return {
            "current_volume": None, "avg_volume": None,
            "rvol": None, "volume_spike": False,
            "volume_direction": "unknown",
            "score": 0, "score_breakdown": {}
        }