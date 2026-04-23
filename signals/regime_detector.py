"""
signals/regime_detector.py — SPY Market Regime Classifier

Determines the current market regime so the daily strategy module
knows WHICH play to run (or to skip the day entirely).

Six regimes:
    1. TRENDING_UP_CALM      → Bull debit/credit spreads (with trend)
    2. TRENDING_DOWN_CALM    → Bear debit/credit spreads (with trend)
    3. CHOPPY_LOW_VOL        → Iron condors (premium harvest)
    4. CHOPPY_HIGH_VOL       → SKIP (vol expansion kills condors)
    5. TRENDING_HIGH_VOL     → Reduced size directional only
    6. EVENT_DAY             → SKIP (FOMC/CPI/NFP/OPEX)

Usage:
    from signals.regime_detector import RegimeDetector
    detector = RegimeDetector()
    result = detector.classify(spy_df, vix_current=14.5, ivr_current=28)

Run standalone smoke test:
    python -m signals.regime_detector
"""

from __future__ import annotations

import os
import sys

# ── Path resolution (matches every other module in this project) ──
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd
from loguru import logger


# ─────────────────────────────────────────
# ENUMS + DATACLASS
# ─────────────────────────────────────────

class Regime(str, Enum):
    TRENDING_UP_CALM   = "trending_up_calm"
    TRENDING_DOWN_CALM = "trending_down_calm"
    CHOPPY_LOW_VOL     = "choppy_low_vol"
    CHOPPY_HIGH_VOL    = "choppy_high_vol"
    TRENDING_HIGH_VOL  = "trending_high_vol"
    EVENT_DAY          = "event_day"
    UNKNOWN            = "unknown"


@dataclass
class RegimeResult:
    regime:          Regime
    tradeable:       bool
    play:            str        # human-readable play name
    confidence:      float      # 0.0 – 1.0
    reasons:         list[str]
    metrics:         dict       # spy_close, ma200, adx, vix, ivr, etc.

    def to_dict(self) -> dict:
        return {
            "regime":     self.regime.value,
            "tradeable":  self.tradeable,
            "play":       self.play,
            "confidence": round(self.confidence, 2),
            "reasons":    self.reasons,
            "metrics":    self.metrics,
        }


# ─────────────────────────────────────────
# THRESHOLDS — tune via backtest, not vibes
# ─────────────────────────────────────────
VIX_CALM_MAX     = 18.0   # Below this = calm
VIX_ELEVATED_MAX = 22.0   # Above this = skip condors / reduce size
ADX_TREND_MIN    = 20.0   # Above this = trending market
TREND_MA_PERIOD  = 200
ADX_PERIOD       = 14


# ─────────────────────────────────────────
# DETECTOR
# ─────────────────────────────────────────

class RegimeDetector:
    """
    Classifies the current SPY regime each morning.
    The strategy layer reads this and decides what to trade (or skip).
    """

    def __init__(self, event_calendar: list[date] | None = None):
        # Inject economic event dates: FOMC, CPI, NFP, OPEX, etc.
        # Build this list from your econ-calendar module or a static config.
        # Empty = no events blocked.
        self.event_calendar: set[date] = set(event_calendar or [])

    # ─────────────────────────────────────────
    # PUBLIC ENTRY
    # ─────────────────────────────────────────

    def classify(
        self,
        spy_daily_df: pd.DataFrame,
        vix_current:  float,
        ivr_current:  float,
        today:        date | None = None,
    ) -> RegimeResult:
        """
        Classify today's regime.

        Args:
            spy_daily_df:  Daily OHLCV DataFrame — needs 200+ rows.
            vix_current:   VIX close or spot value (float).
            ivr_current:   SPY IV Rank 0–100 (float).
            today:         Date to check against event calendar.
                           Defaults to date.today().

        Returns:
            RegimeResult with regime, play recommendation, and metrics.
        """
        today = today or date.today()
        reasons: list[str] = []

        # ── 1. Event calendar check — overrides everything ─────
        if today in self.event_calendar:
            logger.info(f"Regime: EVENT_DAY — {today}")
            return RegimeResult(
                regime     = Regime.EVENT_DAY,
                tradeable  = False,
                play       = "SKIP — major event today (FOMC / CPI / NFP / OPEX)",
                confidence = 1.0,
                reasons    = ["Event on calendar — vol behavior unpredictable"],
                metrics    = {"vix": vix_current, "ivr": ivr_current},
            )

        # ── 2. Data validation ─────────────────────────────────
        if spy_daily_df is None or len(spy_daily_df) < TREND_MA_PERIOD:
            logger.warning("RegimeDetector: insufficient data")
            return RegimeResult(
                regime     = Regime.UNKNOWN,
                tradeable  = False,
                play       = "SKIP — insufficient historical data",
                confidence = 0.0,
                reasons    = [f"Need {TREND_MA_PERIOD}+ daily bars, got "
                              f"{len(spy_daily_df) if spy_daily_df is not None else 0}"],
                metrics    = {},
            )

        # ── 3. Compute metrics ─────────────────────────────────
        spy_close   = float(spy_daily_df["close"].iloc[-1])
        ma200       = float(spy_daily_df["close"].rolling(TREND_MA_PERIOD).mean().iloc[-1])
        adx         = self._compute_adx(spy_daily_df, ADX_PERIOD)
        above_ma    = spy_close > ma200
        ma_dist_pct = round((spy_close - ma200) / ma200 * 100, 2)

        metrics = {
            "spy_close":    round(spy_close, 2),
            "ma200":        round(ma200, 2),
            "ma200_dist_%": ma_dist_pct,
            "adx":          round(adx, 2),
            "vix":          round(vix_current, 2),
            "ivr":          round(ivr_current, 2),
        }

        # ── 4. Decision tree ───────────────────────────────────
        is_trending = adx >= ADX_TREND_MIN
        is_calm     = vix_current <  VIX_CALM_MAX
        is_elevated = vix_current >= VIX_ELEVATED_MAX

        # CHOPPY + HIGH VOL → skip (condor poison)
        if not is_trending and is_elevated:
            reasons += [
                f"ADX {adx:.1f} < {ADX_TREND_MIN} (no trend)",
                f"VIX {vix_current:.1f} ≥ {VIX_ELEVATED_MAX} — vol expanding in chop",
                "Iron condors fail when vol expands without direction",
            ]
            return RegimeResult(
                Regime.CHOPPY_HIGH_VOL, False,
                "SKIP — high vol chop is condor poison",
                0.9, reasons, metrics,
            )

        # CHOPPY + CALM → iron condor
        if not is_trending and is_calm:
            reasons += [
                f"ADX {adx:.1f} < {ADX_TREND_MIN} (choppy / range-bound)",
                f"VIX {vix_current:.1f} < {VIX_CALM_MAX} (calm, low realized vol)",
                "Range environment favours premium selling",
            ]
            return RegimeResult(
                Regime.CHOPPY_LOW_VOL, True,
                "IRON CONDOR — sell premium inside the range",
                0.85, reasons, metrics,
            )

        # CHOPPY + TRANSITION ZONE (VIX 18–22) → half-size condor
        if not is_trending and not is_calm and not is_elevated:
            reasons += [
                f"ADX {adx:.1f} — choppy, no clear trend",
                f"VIX {vix_current:.1f} in transition zone (18–22)",
                "Half-size condor or sit out — vol direction unclear",
            ]
            return RegimeResult(
                Regime.CHOPPY_LOW_VOL, True,
                "REDUCED IRON CONDOR — half size, tighter wings",
                0.50, reasons, metrics,
            )

        # TRENDING UP
        if is_trending and above_ma:
            reasons += [
                f"ADX {adx:.1f} ≥ {ADX_TREND_MIN} (trending)",
                f"SPY {ma_dist_pct:+.1f}% above 200MA (uptrend confirmed)",
            ]
            if is_elevated:
                reasons.append(f"VIX {vix_current:.1f} elevated — 50% size, widen stops")
                return RegimeResult(
                    Regime.TRENDING_HIGH_VOL, True,
                    "BULL DEBIT SPREAD — half size (elevated vol)",
                    0.65, reasons, metrics,
                )
            play = (
                "BULL PUT CREDIT SPREAD — IVR elevated, sell the put side"
                if ivr_current >= 50 else
                "BULL CALL DEBIT SPREAD — low IVR, cheap calls, buy the move"
            )
            reasons.append(
                f"IVR {ivr_current:.0f} {'≥' if ivr_current >= 50 else '<'} 50 "
                f"→ {'sell premium' if ivr_current >= 50 else 'buy directional'}"
            )
            return RegimeResult(
                Regime.TRENDING_UP_CALM, True, play, 0.85, reasons, metrics,
            )

        # TRENDING DOWN
        if is_trending and not above_ma:
            reasons += [
                f"ADX {adx:.1f} ≥ {ADX_TREND_MIN} (trending)",
                f"SPY {ma_dist_pct:+.1f}% below 200MA (downtrend confirmed)",
            ]
            if is_elevated:
                reasons.append(f"VIX {vix_current:.1f} elevated — 50% size, widen stops")
                return RegimeResult(
                    Regime.TRENDING_HIGH_VOL, True,
                    "BEAR DEBIT SPREAD — half size (elevated vol)",
                    0.65, reasons, metrics,
                )
            play = (
                "BEAR CALL CREDIT SPREAD — IVR elevated, sell the call side"
                if ivr_current >= 50 else
                "BEAR PUT DEBIT SPREAD — low IVR, cheap puts, buy the move down"
            )
            reasons.append(
                f"IVR {ivr_current:.0f} {'≥' if ivr_current >= 50 else '<'} 50 "
                f"→ {'sell premium' if ivr_current >= 50 else 'buy directional'}"
            )
            return RegimeResult(
                Regime.TRENDING_DOWN_CALM, True, play, 0.85, reasons, metrics,
            )

        # Fallback — should never reach here
        logger.warning("RegimeDetector: decision tree fell through — check inputs")
        return RegimeResult(
            Regime.UNKNOWN, False, "SKIP — no clear regime",
            0.3, ["Decision tree fell through — review inputs"], metrics,
        )

    # ─────────────────────────────────────────
    # ADX — Wilder's Average Directional Index
    # ─────────────────────────────────────────

    @staticmethod
    def _compute_adx(df: pd.DataFrame, period: int = 14) -> float:
        """
        Compute Wilder's ADX. Returns 0.0 if insufficient data.
        ADX measures trend STRENGTH, not direction.
        > 20 = trending, > 25 = strong trend, < 20 = choppy.
        """
        if len(df) < period * 2:
            return 0.0

        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        plus_dm  = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm[plus_dm  <  minus_dm] = 0
        minus_dm[minus_dm <= plus_dm] = 0

        tr = pd.concat([
            (high - low),
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)

        atr      = tr.rolling(period).mean()
        plus_di  = 100 * (plus_dm.rolling(period).mean()  / atr.replace(0, 1))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1))
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        adx      = dx.rolling(period).mean().iloc[-1]

        return float(adx) if pd.notna(adx) else 0.0


# ─────────────────────────────────────────
# STANDALONE SMOKE TEST
# ─────────────────────────────────────────

if __name__ == "__main__":
    import numpy as np

    rng    = np.random.default_rng(42)
    closes = 400 + np.cumsum(rng.normal(0.3, 1.5, 250))
    df     = pd.DataFrame({
        "open":   closes - 0.5,
        "high":   closes + 1.0,
        "low":    closes - 1.0,
        "close":  closes,
        "volume": rng.integers(50_000_000, 100_000_000, 250),
    })

    detector = RegimeDetector()
    cases = [
        (13.5, 22, "calm, low IVR → bull debit spread"),
        (14.0, 62, "calm, high IVR → bull credit spread"),
        (24.5, 38, "elevated vol trending → half size"),
        (16.0, 30, "calm, mid IVR → bull debit spread"),
    ]

    for vix, ivr, label in cases:
        r = detector.classify(df, vix_current=vix, ivr_current=ivr)
        print(f"\n── {label} ──")
        print(f"  Regime:     {r.regime.value}")
        print(f"  Play:       {r.play}")
        print(f"  Confidence: {r.confidence:.0%}")
        for reason in r.reasons:
            print(f"  • {reason}")
