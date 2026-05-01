"""
signals/spy_options_engine.py  (v2 — Balanced Calls / Puts / Iron Condors)

SPY-specific options signal engine targeting three equal strategies:
  1. Call Debit Spread  — bullish directional momentum
  2. Put Debit Spread   — bearish directional momentum
  3. Iron Condor        — range-bound, time decay play

SCORING PHILOSOPHY:
  Each strategy has its OWN independent scoring system.
  A strong bearish day fires a put spread alert.
  A choppy day fires an iron condor alert.
  A strong bullish day fires a call spread alert.
  They are NOT competing — all three are evaluated every scan.

THRESHOLDS (from config.py):
  score >= 45 → Standard alert
  score >= 68 → High conviction alert

Usage:
    from signals.spy_options_engine import SPYOptionsEngine
    engine = SPYOptionsEngine()
    setups = engine.analyze(df_daily, df_intraday)
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Literal, Optional
from loguru import logger
import config


# ─────────────────────────────────────────
# RESULT DATACLASS
# ─────────────────────────────────────────

@dataclass
class SPYSetup:
    """Holds all data for a single SPY options setup candidate."""

    strategy:   Literal["call_debit_spread", "put_debit_spread", "iron_condor"]
    conviction: Literal["high", "standard", "watch"]
    timeframe:  Literal["swing", "intraday", "both"]
    score:      int
    reasons:    list

    direction:    Optional[str]   = None
    spy_price:    Optional[float] = None
    rsi:          Optional[float] = None
    rvol:         Optional[float] = None
    atr:          Optional[float] = None
    trend:        Optional[str]   = None

    # Debit spread fields
    long_strike:  Optional[float] = None
    short_strike: Optional[float] = None
    est_debit:    Optional[float] = None
    max_profit:   Optional[float] = None
    max_loss:     Optional[float] = None
    spread_rr:    Optional[float] = None

    # Iron condor fields
    ic_put_long:    Optional[float] = None
    ic_put_short:   Optional[float] = None
    ic_call_short:  Optional[float] = None
    ic_call_long:   Optional[float] = None
    ic_credit:      Optional[float] = None
    ic_profit_zone: Optional[str]   = None

    def to_discord_msg(self) -> str:
        """Format this setup as a Discord-ready message string."""
        icons  = {
            "call_debit_spread": "📈",
            "put_debit_spread":  "📉",
            "iron_condor":       "🦅",
        }
        labels = {
            "call_debit_spread": "CALL DEBIT SPREAD",
            "put_debit_spread":  "PUT DEBIT SPREAD",
            "iron_condor":       "IRON CONDOR",
        }
        conviction_tag = (
            "🔥 **HIGH CONVICTION**"
            if self.conviction == "high"
            else "📌 **Standard Alert**"
        )

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"{icons[self.strategy]} **SPY — {labels[self.strategy]}**",
            f"{conviction_tag} | {self.timeframe.upper()} | Score: **{self.score}/100**",
            "",
            "📊 **Market Snapshot**",
            f"  Price: **${self.spy_price:.2f}**"    if self.spy_price else None,
            f"  Trend: {self.trend}"                  if self.trend     else None,
            f"  RSI:   {self.rsi:.1f}"                if self.rsi       else None,
            f"  RVOL:  {self.rvol:.2f}x avg"          if self.rvol      else None,
            f"  ATR:   ${self.atr:.2f} (avg daily range)" if self.atr   else None,
            "",
        ]

        if self.strategy in ("call_debit_spread", "put_debit_spread"):
            opt = "CALL" if self.strategy == "call_debit_spread" else "PUT"
            lines += [
                f"🎯 **Spread Legs**  ({config.DTE_SWING_RECOMMENDED} DTE suggested)",
                f"  BUY  {opt} ${self.long_strike:.0f}   ← directional exposure",
                f"  SELL {opt} ${self.short_strike:.0f}   ← reduces net cost",
                "",
                "💰 **Estimated Risk/Reward**",
                f"  Net Debit:   ~${self.est_debit:.2f}/share (~${self.max_loss:.0f}/contract)",
                f"  Max Profit:  ~${self.max_profit:.0f}/contract",
                f"  Max Loss:    ~${self.max_loss:.0f}/contract (premium paid)",
                f"  Spread R/R:  ~{self.spread_rr:.1f}:1",
                "",
                "📋 **Exit Rules**",
                "  ✅ Close at 50-65% of max profit",
                "  🛑 Cut at 50% loss of debit paid",
                "  ⏰ Exit with 7+ DTE remaining (theta crush)",
            ]

        elif self.strategy == "iron_condor":
            lines += [
                f"🎯 **Condor Legs**  ({config.DTE_SWING_RECOMMENDED} DTE suggested)",
                f"  BUY  PUT  ${self.ic_put_long:.0f}   ← lower wing (protection)",
                f"  SELL PUT  ${self.ic_put_short:.0f}   ← lower short (collect premium)",
                f"  SELL CALL ${self.ic_call_short:.0f}   ← upper short (collect premium)",
                f"  BUY  CALL ${self.ic_call_long:.0f}   ← upper wing (protection)",
                "",
                "💰 **Estimated Risk/Reward**",
                f"  Net Credit:   ~${self.ic_credit:.2f}/share per side",
                f"  Profit Zone:  {self.ic_profit_zone}" if self.ic_profit_zone else None,
                "  Max Profit:   Keep full credit (SPY stays in range at expiry)",
                "  Max Loss:     Wing width minus total credit received",
                "",
                "📋 **Exit Rules**",
                "  ✅ Close at 50% of max credit",
                "  🛑 Exit immediately if either short strike breached",
                "  ⏰ Exit with 7+ DTE remaining",
            ]

        lines += [
            "",
            "🔍 **Why This Setup Fired**",
        ]
        for r in self.reasons:
            lines.append(f"  • {r}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(ln for ln in lines if ln is not None)


# ─────────────────────────────────────────
# INDICATOR HELPERS
# ─────────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean()


def _emas(close: pd.Series) -> dict:
    return {
        "ema9":  close.ewm(span=9,  adjust=False).mean(),
        "ema21": close.ewm(span=21, adjust=False).mean(),
        "ema50": close.ewm(span=50, adjust=False).mean(),
    }


def _rvol(volume: pd.Series, lookback: int = 20) -> float:
    avg = volume.iloc[-(lookback + 1):-1].mean()
    return round(float(volume.iloc[-1] / avg), 2) if avg > 0 else 1.0


def _extract_context(df: pd.DataFrame) -> dict:
    close   = df["close"]
    emas    = _emas(close)
    rsi_s   = _rsi(close)
    atr_s   = _atr(df)
    vol_r   = _rvol(df["volume"])

    price   = float(close.iloc[-1])
    e9      = float(emas["ema9"].iloc[-1])
    e21     = float(emas["ema21"].iloc[-1])
    e50     = float(emas["ema50"].iloc[-1])
    rsi     = float(rsi_s.iloc[-1])
    rsi_p   = float(rsi_s.iloc[-2])
    atr_val = float(atr_s.iloc[-1])
    candle  = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)

    recent      = close.tail(10)
    range_pct   = float((recent.max() - recent.min()) / recent.mean() * 100)
    ema_gap_pct = abs(e9 - e21) / e21 * 100

    return dict(
        price=price, e9=e9, e21=e21, e50=e50,
        rsi=rsi, rsi_prev=rsi_p,
        atr=atr_val, rvol=vol_r,
        candle_pct=candle,
        range_pct_10=range_pct,
        ema_gap_pct=ema_gap_pct,
    )


# ─────────────────────────────────────────
# STRATEGY SCORERS (each max 100 pts)
# ─────────────────────────────────────────

def _score_call_spread(ctx: dict) -> tuple[int, list]:
    """Bullish directional. Fires on uptrend + momentum confirmation."""
    score, reasons = 0, []
    p, e9, e21, e50 = ctx["price"], ctx["e9"], ctx["e21"], ctx["e50"]
    rsi, rsi_p      = ctx["rsi"], ctx["rsi_prev"]
    candle, rvol    = ctx["candle_pct"], ctx["rvol"]

    # Trend (35 pts)
    if p > e9:   score += 12; reasons.append("Price above EMA9")
    if e9 > e21: score += 12; reasons.append("EMA9 > EMA21 — short-term uptrend")
    if p > e50:  score += 7;  reasons.append("Price above EMA50")
    if e21 > e50: score += 4; reasons.append("EMA21 > EMA50 — trend confirmed")

    # Momentum (35 pts)
    if 52 < rsi < 75:
        score += 15; reasons.append(f"RSI bullish zone ({rsi:.1f})")
    elif rsi >= 45:
        score += 7;  reasons.append(f"RSI above midline ({rsi:.1f})")
    if rsi > rsi_p:
        score += 8;  reasons.append("RSI rising")
    if candle >= 0.5:
        score += 12; reasons.append(f"Strong up candle (+{candle:.2f}%)")
    elif candle >= 0.2:
        score += 6;  reasons.append(f"Positive candle (+{candle:.2f}%)")

    # Volume (30 pts)
    if rvol >= 1.5:   score += 30; reasons.append(f"Strong volume ({rvol:.1f}x avg)")
    elif rvol >= 1.2: score += 20; reasons.append(f"Above-avg volume ({rvol:.1f}x)")
    elif rvol >= 0.85: score += 10; reasons.append(f"Normal volume ({rvol:.1f}x)")
    else:             score += 3;  reasons.append(f"Light volume ({rvol:.1f}x) ⚠️")

    return min(score, 100), reasons


def _score_put_spread(ctx: dict) -> tuple[int, list]:
    """
    Bearish directional. SYMMETRIC to call spread — equal trigger difficulty.
    Fires on downtrend + selling pressure confirmation.
    """
    score, reasons = 0, []
    p, e9, e21, e50 = ctx["price"], ctx["e9"], ctx["e21"], ctx["e50"]
    rsi, rsi_p      = ctx["rsi"], ctx["rsi_prev"]
    candle, rvol    = ctx["candle_pct"], ctx["rvol"]

    # Trend (35 pts) — mirror of call spread
    if p < e9:    score += 12; reasons.append("Price below EMA9")
    if e9 < e21:  score += 12; reasons.append("EMA9 < EMA21 — short-term downtrend")
    if p < e50:   score += 7;  reasons.append("Price below EMA50")
    if e21 < e50: score += 4;  reasons.append("EMA21 < EMA50 — downtrend confirmed")

    # Momentum (35 pts) — mirror of call spread
    if 25 < rsi < 48:
        score += 15; reasons.append(f"RSI bearish zone ({rsi:.1f})")
    elif rsi <= 55:
        score += 7;  reasons.append(f"RSI below midline ({rsi:.1f})")
    if rsi < rsi_p:
        score += 8;  reasons.append("RSI falling — selling pressure building")
    if candle <= -0.5:
        score += 12; reasons.append(f"Strong down candle ({candle:.2f}%)")
    elif candle <= -0.2:
        score += 6;  reasons.append(f"Negative candle ({candle:.2f}%)")

    # Volume (30 pts) — mirror of call spread
    if rvol >= 1.5:    score += 30; reasons.append(f"Heavy volume on down move ({rvol:.1f}x)")
    elif rvol >= 1.2:  score += 20; reasons.append(f"Above-avg volume ({rvol:.1f}x)")
    elif rvol >= 0.85: score += 10; reasons.append(f"Normal volume ({rvol:.1f}x)")
    else:              score += 3;  reasons.append(f"Light volume ({rvol:.1f}x) ⚠️")

    return min(score, 100), reasons


def _score_iron_condor(ctx: dict) -> tuple[int, list]:
    """
    Range-bound / premium collection.
    Fires when SPY is consolidating with no directional bias.
    Has its OWN scoring logic — not competing with directional spreads.
    """
    score, reasons = 0, []
    rng     = ctx["range_pct_10"]
    rsi     = ctx["rsi"]
    ema_gap = ctx["ema_gap_pct"]

    # Range tightness (40 pts) — tight price action = good IC candidate
    if rng <= 1.5:
        score += 40; reasons.append(f"Very tight range ({rng:.1f}% over 10 days) — ideal IC")
    elif rng <= config.IC_RANGE_THRESHOLD_PCT:
        score += 28; reasons.append(f"Tight range ({rng:.1f}% over 10 days)")
    elif rng <= 4.0:
        score += 14; reasons.append(f"Moderate range ({rng:.1f}%) — borderline IC")

    # RSI neutrality (30 pts) — no directional pressure = premium decay
    if 44 < rsi < 56:
        score += 30; reasons.append(f"RSI perfectly neutral ({rsi:.1f})")
    elif config.IC_RSI_LOW < rsi < config.IC_RSI_HIGH:
        score += 20; reasons.append(f"RSI near neutral ({rsi:.1f})")
    elif 35 < rsi < 65:
        score += 10; reasons.append(f"RSI borderline neutral ({rsi:.1f})")

    # EMA convergence (30 pts) — flat EMAs = no trend = sell premium
    if ema_gap < 0.2:
        score += 30; reasons.append("EMA9/21 nearly flat — confirmed range-bound")
    elif ema_gap < 0.5:
        score += 20; reasons.append("EMA9/21 converging")
    elif ema_gap < 1.0:
        score += 10; reasons.append("EMAs mildly separated — weakening trend")

    return min(score, 100), reasons


# ─────────────────────────────────────────
# LEVEL BUILDERS
# ─────────────────────────────────────────

def _build_debit_spread(price: float, direction: str) -> dict:
    w = config.SPY_SPREAD_WIDTH

    def rnd(x): return float(round(x))

    if direction == "bullish":
        long_s, short_s = rnd(price), rnd(price + w)
    else:
        long_s, short_s = rnd(price), rnd(price - w)

    est_debit  = round(w * 0.42, 2)
    max_loss   = round(est_debit * 100, 2)
    max_profit = round((w - est_debit) * 100, 2)
    spread_rr  = round(max_profit / max_loss, 2) if max_loss > 0 else 0

    return dict(
        long_strike=long_s, short_strike=short_s,
        est_debit=est_debit, max_loss=max_loss,
        max_profit=max_profit, spread_rr=spread_rr,
    )


def _build_iron_condor(price: float, atr_val: float) -> dict:
    w = config.SPY_SPREAD_WIDTH

    def rnd(x): return float(round(x))

    call_short = rnd(price + atr_val * 1.2)
    put_short  = rnd(price - atr_val * 1.2)
    call_long  = rnd(call_short + w)
    put_long   = rnd(put_short - w)
    est_credit = round(w * 0.18, 2)

    return dict(
        ic_put_long=put_long, ic_put_short=put_short,
        ic_call_short=call_short, ic_call_long=call_long,
        ic_credit=est_credit,
        ic_profit_zone=f"${put_short:.0f} – ${call_short:.0f}",
    )


# ─────────────────────────────────────────
# CONFLUENCE CHECK
# ─────────────────────────────────────────

def _intraday_confirms(df: pd.DataFrame, direction: str) -> bool:
    if df is None or len(df) < 20:
        return False
    close = df["close"]
    emas  = _emas(close)
    e9    = float(emas["ema9"].iloc[-1])
    e21   = float(emas["ema21"].iloc[-1])
    p     = float(close.iloc[-1])
    if direction == "bullish":
        return p > e9 > e21
    elif direction == "bearish":
        return p < e9 < e21
    elif direction == "neutral":
        return abs(e9 - e21) / e21 * 100 < 0.5
    return False


# ─────────────────────────────────────────
# MAIN ENGINE
# ─────────────────────────────────────────

class SPYOptionsEngine:
    """
    Evaluates all three SPY strategies independently.
    No strategy is preferred — each has balanced scoring.
    """

    def analyze(
        self,
        df_daily:    pd.DataFrame,
        df_intraday: Optional[pd.DataFrame] = None,
    ) -> list[SPYSetup]:
        """Score all three strategies and return qualifying SPYSetup objects."""
        if df_daily is None or len(df_daily) < 30:
            logger.warning("SPYOptionsEngine: not enough data")
            return []

        ctx   = _extract_context(df_daily)
        price = ctx["price"]
        atr   = ctx["atr"]

        if price > ctx["e9"] > ctx["e21"]:
            trend_lbl = "📈 Uptrend"
        elif price < ctx["e9"] < ctx["e21"]:
            trend_lbl = "📉 Downtrend"
        else:
            trend_lbl = "➡️ Sideways"

        common = dict(
            spy_price=round(price, 2),
            rsi=round(ctx["rsi"], 1),
            rvol=ctx["rvol"],
            atr=round(atr, 2),
            trend=trend_lbl,
        )

        results: list[SPYSetup] = []

        # 1. CALL DEBIT SPREAD
        cs, cr = _score_call_spread(ctx)
        tf = "swing"
        if _intraday_confirms(df_intraday, "bullish"):
            cs = min(int(cs * config.CONFLUENCE_BONUS_MULTIPLIER), 100)
            cr.append("✅ 15m confirms bullish direction")
            tf = "both"
        logger.debug(f"SPY call_debit_spread score={cs}")
        if cs >= config.SCORE_ALERT_MINIMUM:
            results.append(SPYSetup(
                strategy="call_debit_spread",
                conviction="high" if cs >= config.SCORE_HIGH_CONVICTION else "standard",
                timeframe=tf, score=cs, reasons=cr, direction="bullish",
                **_build_debit_spread(price, "bullish"), **common,
            ))

        # 2. PUT DEBIT SPREAD
        ps, pr = _score_put_spread(ctx)
        tf = "swing"
        if _intraday_confirms(df_intraday, "bearish"):
            ps = min(int(ps * config.CONFLUENCE_BONUS_MULTIPLIER), 100)
            pr.append("✅ 15m confirms bearish direction")
            tf = "both"
        logger.debug(f"SPY put_debit_spread  score={ps}")
        if ps >= config.SCORE_ALERT_MINIMUM:
            results.append(SPYSetup(
                strategy="put_debit_spread",
                conviction="high" if ps >= config.SCORE_HIGH_CONVICTION else "standard",
                timeframe=tf, score=ps, reasons=pr, direction="bearish",
                **_build_debit_spread(price, "bearish"), **common,
            ))

        # 3. IRON CONDOR
        ics, icr = _score_iron_condor(ctx)
        tf = "swing"
        if _intraday_confirms(df_intraday, "neutral"):
            ics = min(int(ics * config.CONFLUENCE_BONUS_MULTIPLIER), 100)
            icr.append("✅ 15m also range-bound — IC confirmed")
            tf = "both"
        logger.debug(f"SPY iron_condor       score={ics}")
        if ics >= config.SCORE_ALERT_MINIMUM:
            results.append(SPYSetup(
                strategy="iron_condor",
                conviction="high" if ics >= config.SCORE_HIGH_CONVICTION else "standard",
                timeframe=tf, score=ics, reasons=icr, direction="neutral",
                **_build_iron_condor(price, atr), **common,
            ))

        results.sort(key=lambda s: s.score, reverse=True)
        return results
