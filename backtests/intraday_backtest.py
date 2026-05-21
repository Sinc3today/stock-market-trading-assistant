"""
backtests/intraday_backtest.py -- Real-priced 0DTE/1DTE backtest.

Phase 1, the make-or-break: does the intraday strategy actually make money on
REAL option prices? Uses options_history (real option intraday aggregates) +
intraday_data (real SPY intraday bars) — no Black-Scholes (BS is unreliable
for 0DTE near expiry).

The agreed design (with the user):
  - Regime-split structure: iron condor on RANGE days, directional debit on
    TREND days.
  - KEY REFRAME: ENGAGE high-vol / strong-direction days on the DIRECTIONAL
    track. The swing strategy skipped them (condors die on big moves) — but a
    defined-risk 0DTE debit's BEST day is a high-direction day.
  - Entry in the morning, informed by the pre-market reaction.
  - Intraday exit: profit target / stop / hard EOD flatten.

Decision + leg construction are pure and unit-tested; the day simulator pulls
real option bars. All time logic is US/Eastern (host is Central).
"""

from __future__ import annotations

import os
import sys
from datetime import date, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from loguru import logger

ET = pytz.timezone("US/Eastern")

# ── Structure parameters (0DTE SPY, $1 strikes) ──────────────────────
CONDOR_SHORT_OTM   = 3.0    # short strikes this many points OTM
CONDOR_WING        = 5.0    # long strike this many points beyond the short
DEBIT_SHORT_OTM    = 3.0    # debit short leg this many points OTM (long is ATM)
# Intraday exit
PROFIT_TARGET_PCT  = 0.50   # 0DTE: take profit faster than swing
STOP_MULT          = 2.0    # stop at this multiple of credit/debit risk
EOD_FLATTEN_ET     = time(15, 45)   # hard flatten — dodge pin/assignment risk
COMMISSION_PER_LEG = 0.65
SLIPPAGE           = 0.05


def decide_structure(regime: str, direction: str, is_high_vol: bool,
                     is_event: bool) -> tuple[str, str] | None:
    """
    Map the daily regime read to an intraday structure for the 0DTE/1DTE
    tracks. Returns (structure, direction) or None to skip.

      range (choppy calm)         -> iron_condor
      trend (up/down calm)        -> directional debit, with the trend
      HIGH-VOL / strong direction -> directional debit (the reframe: engage,
                                      don't skip — defined-risk catches the move)
      event day                   -> directional debit ONLY (condors die on
                                      the release; a debit can ride the move)

    structure in {"iron_condor","bull_debit","bear_debit"}.
    """
    r = (regime or "").lower()

    # High-vol or event days: directional only (engage the direction).
    if is_high_vol or is_event:
        if direction == "bullish":
            return ("bull_debit", "bullish")
        if direction == "bearish":
            return ("bear_debit", "bearish")
        return None   # no directional read on a high-vol/event day → stand down

    # Range day → condor.
    if "choppy" in r:
        return ("iron_condor", "neutral")

    # Trend day → directional debit with the trend.
    if "trending_up" in r:
        return ("bull_debit", "bullish")
    if "trending_down" in r:
        return ("bear_debit", "bearish")

    return None


def build_0dte_legs(spot: float, structure: str) -> list[dict]:
    """
    Construct 0DTE leg specs at point-offsets from spot, rounded to SPY's
    $1 strikes. Returns [{action, cp, strike}].
    """
    def k(x): return round(spot + x)   # SPY strikes are $1-wide
    if structure == "iron_condor":
        return [
            {"action": "SELL", "cp": "P", "strike": k(-CONDOR_SHORT_OTM)},
            {"action": "BUY",  "cp": "P", "strike": k(-CONDOR_SHORT_OTM - CONDOR_WING)},
            {"action": "SELL", "cp": "C", "strike": k(+CONDOR_SHORT_OTM)},
            {"action": "BUY",  "cp": "C", "strike": k(+CONDOR_SHORT_OTM + CONDOR_WING)},
        ]
    if structure == "bull_debit":   # buy ATM call, sell OTM call
        return [
            {"action": "BUY",  "cp": "C", "strike": k(0)},
            {"action": "SELL", "cp": "C", "strike": k(+DEBIT_SHORT_OTM)},
        ]
    if structure == "bear_debit":   # buy ATM put, sell OTM put
        return [
            {"action": "BUY",  "cp": "P", "strike": k(0)},
            {"action": "SELL", "cp": "P", "strike": k(-DEBIT_SHORT_OTM)},
        ]
    return []


def is_credit_structure(structure: str) -> bool:
    return structure == "iron_condor"


def _to_et(df):
    """Index a UTC-bar DataFrame in US/Eastern."""
    idx = df.index
    df = df.copy()
    df.index = (idx.tz_localize("UTC").tz_convert(ET) if idx.tz is None
                else idx.tz_convert(ET))
    return df


def _spread_value(legs_closes: list[tuple[dict, float]], structure: str) -> float:
    """Signed mark (per share) of the spread from each leg's option price:
    long legs add, short legs subtract."""
    long_v = short_v = 0.0
    for leg, px in legs_closes:
        if leg["action"] == "BUY":
            long_v += px
        else:
            short_v += px
    if is_credit_structure(structure):
        return max(0.0, short_v - long_v)   # cost to buy the condor back
    return max(0.0, long_v - short_v)        # value of the debit spread


def simulate_0dte_day(day: date, structure: str, spy_intraday, options_history,
                      entry_et: time = time(9, 35)) -> dict | None:
    """
    Real-priced 0DTE simulation for one day. Returns realized P&L dict or None
    if the day can't be priced (no SPY session bars, or missing option data).
    """
    from data.options_history import option_ticker

    if spy_intraday is None or spy_intraday.empty:
        return None
    spy = _to_et(spy_intraday)
    session = spy[(spy.index.time >= entry_et) & (spy.index.time <= EOD_FLATTEN_ET)]
    if session.empty:
        return None
    entry_ts   = session.index[0]
    entry_spot = float(session.iloc[0]["close"])

    legs = build_0dte_legs(entry_spot, structure)
    if not legs:
        return None

    # Pull each leg's real intraday option bars (expiry == day for 0DTE).
    leg_closes: list[tuple[dict, "pd.Series"]] = []
    for leg in legs:
        contract = option_ticker("SPY", day, leg["cp"], leg["strike"])
        df = options_history.get_aggs(contract, 5, "minute", day, day)
        if df.empty:
            return None   # illiquid strike / no data → can't price honestly
        s = _to_et(df)["close"]
        leg_closes.append((leg, s))

    def marks_at(ts):
        out = []
        for leg, s in leg_closes:
            at = s[s.index <= ts]
            if at.empty:
                return None
            out.append((leg, float(at.iloc[-1])))
        return out

    credit = is_credit_structure(structure)
    entry_marks = marks_at(entry_ts)
    if entry_marks is None:
        return None
    entry_px = _spread_value(entry_marks, structure)
    entry_px = (entry_px - SLIPPAGE) if credit else (entry_px + SLIPPAGE)
    if entry_px <= 0:
        return None
    width      = abs(legs[0]["strike"] - legs[1]["strike"]) if len(legs) >= 2 else 0
    max_profit = entry_px * 100 if credit else (width - entry_px) * 100
    n_legs     = len(legs)
    commission = COMMISSION_PER_LEG * n_legs * 2

    # Walk the session, mark the spread, exit on target / stop / EOD.
    exit_reason = "eod"
    pnl = -commission
    for ts in session.index:
        m = marks_at(ts)
        if m is None:
            continue
        val = _spread_value(m, structure)
        if credit:
            cost = val + SLIPPAGE
            pnl  = (entry_px - cost) * 100 - commission
        else:
            proceeds = max(0.0, val - SLIPPAGE)
            pnl      = (proceeds - entry_px) * 100 - commission
        if max_profit > 0 and pnl >= PROFIT_TARGET_PCT * max_profit:
            exit_reason = "target"; break
        if pnl <= -STOP_MULT * max_profit:
            exit_reason = "stop"; break

    return {
        "date": day.isoformat(), "structure": structure,
        "entry_spot": round(entry_spot, 2), "entry_px": round(entry_px, 2),
        "pnl_dollars": round(pnl, 2),
        "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "exit_reason": exit_reason,
    }


def run_intraday_backtest(from_date: date, to_date: date,
                          event_dates: set | None = None,
                          max_concurrent: int = 99) -> "pd.DataFrame":
    """
    Real-priced 0DTE backtest over a date range. Uses the daily RegimeDetector
    classification (no lookahead) for the structure decision — INCLUDING
    engaging high-vol days directionally (the reframe), independent of the
    swing strategy's skip flag. Pulls real SPY + option intraday data per day.

    Slow on first run (real option fetches per leg per day); cache warms it.
    """
    import pandas as pd
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar
    from data.intraday_data import get_stock_intraday
    from data.options_history import OptionsHistory

    event_dates = event_dates or set()
    loader = BacktestDataLoader()
    spy_df, vix_df = loader.load(years=5, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()
    regime_df["date"] = pd.to_datetime(regime_df["date"])

    oh = OptionsHistory()
    rows: list[dict] = []
    for _, r in regime_df.iterrows():
        d = r["date"].date()
        if not (from_date <= d <= to_date):
            continue
        regime    = str(r["regime"])
        direction = "bullish" if (r.get("ma200_dist", 0) or 0) >= 0 else "bearish"
        is_hv     = regime == "trending_high_vol"
        is_event  = d in event_dates
        decision  = decide_structure(regime, direction, is_hv, is_event)
        if decision is None:
            continue
        structure, _ = decision
        spy_intraday = get_stock_intraday("SPY", 5, "minute", d, d, use_cache=True)
        res = simulate_0dte_day(d, structure, spy_intraday, oh)
        if res:
            res["regime"] = regime
            rows.append(res)
    return pd.DataFrame(rows)
