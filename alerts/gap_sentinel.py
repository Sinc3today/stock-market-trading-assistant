"""alerts/gap_sentinel.py -- Sunday-night weekend-gap early warning.

Futures study (docs/FUTURES_STUDY section in GAP_CONDITIONAL_STUDY.md): by
Sunday ~10 PM ET, ES futures show Monday's SPY open gap at corr 0.75 — 82% of
meaningful gaps (>0.5%) are already visible. If a gap is forming AND the user
holds short-strike positions, warn ~11 hours before the open instead of at the
09:15 pre-market check.
"""
from __future__ import annotations

from loguru import logger

GAP_ALERT_PCT = 0.5


def es_weekend_move():
    """(move_pct, es_now, es_fri_close) from ES futures, or None on any failure."""
    try:
        import yfinance as yf
        h = yf.Ticker("ES=F").history(period="5d", interval="1h")
        if h is None or len(h) < 10:
            return None
        import pandas as pd
        h.index = pd.to_datetime(h.index).tz_convert("US/Eastern")
        days = sorted({ts.date() for ts in h.index})
        fri = [d for d in days if pd.Timestamp(d).weekday() == 4]
        if not fri:
            return None
        fri_close = float(h[h.index.date == fri[-1]]["Close"].iloc[-1])
        now = float(h["Close"].iloc[-1])
        return ((now - fri_close) / fri_close * 100, now, fri_close)
    except Exception as e:
        logger.warning(f"gap_sentinel: ES fetch failed: {e}")
        return None


def check_sunday_gap(recorder, send_fn, *, move_fn=es_weekend_move,
                     threshold_pct: float = GAP_ALERT_PCT) -> bool:
    """If futures show a forming weekend gap beyond threshold and the user has
    open positions with short strikes, push an early warning. Returns True if
    an alert fired."""
    open_pos = [t for t in recorder.get_open_trades()
                if (t.get("book") in ("live", "disciplined"))
                and any((l.get("action") or "").upper().startswith("S")
                        for l in (t.get("legs") or []))]
    if not open_pos:
        return False
    r = move_fn()
    if not r:
        return False
    move, now, fri = r
    if abs(move) < threshold_pct:
        logger.info(f"gap_sentinel: ES {move:+.2f}% from Friday — calm weekend")
        return False
    from alerts.stop_watchdog import short_strikes
    lines = [f"ES futures {move:+.2f}% from Friday's close — Monday gap forming "
             f"(Sunday-night reads catch 82% of >0.5% gaps)."]
    for t in open_pos:
        sp, sc = short_strikes(t.get("legs") or [])
        lines.append(f"• {t.get('ticker','SPY')} {str(t.get('strategy','')).replace('_',' ')} "
                     f"shorts {sp:g}/{sc:g}" if sp and sc else
                     f"• {t.get('ticker','SPY')} {str(t.get('strategy','')).replace('_',' ')}")
    lines.append("Nothing to do tonight — plan the open. The 09:15 gap check re-fires pre-market.")
    send_fn(title=f"🌙 Weekend gap forming: ES {move:+.1f}%",
            message="\n".join(lines), priority=1)
    logger.warning(f"gap_sentinel: alerted — ES {move:+.2f}%")
    return True
