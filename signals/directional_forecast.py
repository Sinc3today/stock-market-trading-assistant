"""signals/directional_forecast.py -- independent next-day directional forecast.

A transparent, falsifiable daily call on SPY's next-day direction, derived purely
from price + VIX — deliberately INDEPENDENT of whatever option structure we trade.
The old daily "prediction" just echoed the strategy (condor -> neutral), so it
measured nothing about directional skill. This builds a real track record:
"up / down / flat", scored on whether direction hit, with a VIX-scaled flat band.

Signal = a small additive lean from the trend stack (price vs MA20/50/200),
5-day momentum, and RSI(14). |net| >= 2 -> directional; otherwise neutral.
`expected_move_pct` is the implied 1-day move (VIX/sqrt(252)) — the sane band a
neutral/flat call is scored against (replaces the broken 0.25% flatness test).
"""
from __future__ import annotations

FLAT_BAND_SIGMA_MULT = 1.0       # neutral "correct" if move within ~1 implied daily sigma
_TRADING_DAYS = 252


def _cmp(a: float, b: float, eps: float = 0.001) -> int:
    """+1 if a meaningfully above b, -1 if below, 0 if within eps (treated flat)."""
    if a > b * (1 + eps):
        return 1
    if a < b * (1 - eps):
        return -1
    return 0


def _rsi(close, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    last_gain = gain.iloc[-1]
    last_loss = loss.iloc[-1]
    if last_loss is None or last_loss == 0 or last_loss != last_loss:  # NaN/zero
        return 100.0 if (last_gain or 0) > 0 else 50.0
    rs = last_gain / last_loss
    return 100.0 - 100.0 / (1.0 + rs)


def forecast_direction(spy_daily_df, vix: float | None = None) -> dict:
    """Return {direction, confidence, expected_move_pct, reasons, net_score}.

    direction in {bullish, bearish, neutral}; expected_move_pct is the VIX-implied
    daily move used as the neutral-scoring band. Pure — price + vix only.
    """
    df = spy_daily_df
    col = "close" if "close" in df.columns else "Close"
    close = df[col].astype(float)
    n = len(close)
    c = float(close.iloc[-1])

    def _ma(w):
        w = min(w, n)
        return float(close.rolling(w).mean().iloc[-1])

    ma20, ma50, ma200 = _ma(20), _ma(50), _ma(200)

    score = 0
    reasons: list[str] = []
    for label, s in (("price vs MA20", _cmp(c, ma20)),
                     ("MA20 vs MA50", _cmp(ma20, ma50)),
                     ("MA50 vs MA200", _cmp(ma50, ma200))):
        score += s
        if s:
            reasons.append(f"{label} {'up' if s > 0 else 'down'}")

    if n > 6:
        ret5 = c / float(close.iloc[-6]) - 1.0
        if ret5 > 0.005:
            score += 1
            reasons.append(f"5d momentum +{ret5 * 100:.1f}%")
        elif ret5 < -0.005:
            score -= 1
            reasons.append(f"5d momentum {ret5 * 100:.1f}%")

    rsi = _rsi(close)
    if rsi > 55:
        score += 1
        reasons.append(f"RSI {rsi:.0f} > 55")
    elif rsi < 45:
        score -= 1
        reasons.append(f"RSI {rsi:.0f} < 45")

    if score >= 2:
        direction = "bullish"
    elif score <= -2:
        direction = "bearish"
    else:
        direction = "neutral"
        reasons.append(f"net lean {score:+d} — no clear directional edge")

    if vix and vix > 0:
        expected_move = (float(vix) / (_TRADING_DAYS ** 0.5)) * FLAT_BAND_SIGMA_MULT
    else:
        rets = close.pct_change().dropna()
        expected_move = (float(rets.tail(20).std()) * 100 * FLAT_BAND_SIGMA_MULT
                         if len(rets) >= 2 else 0.8)

    return {
        "direction": direction,
        "confidence": round(min(0.9, 0.4 + abs(score) * 0.1), 2),
        "expected_move_pct": round(expected_move, 3),
        "reasons": reasons,
        "net_score": score,
    }
