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

# ── Structure parameters sourced from the shared builder ─────────────
from signals.intraday_structure_builder import (
    select_legs as _select_legs,
    CONDOR_SHORT_OTM, CONDOR_WING, DEBIT_SHORT_OTM,
)

ET = pytz.timezone("US/Eastern")
# Intraday exit
PROFIT_TARGET_PCT  = 0.50   # 0DTE: take profit faster than swing
STOP_MULT          = 2.0    # stop at this multiple of credit/debit risk
EOD_FLATTEN_ET     = time(15, 45)   # hard flatten — dodge pin/assignment risk
COMMISSION_PER_LEG = 0.65
SLIPPAGE           = 0.05
# Entry confirmation (the blend: opening-range + VWAP)
MARKET_OPEN_ET     = time(9, 30)
OR_MINUTES         = 15            # opening range = first 15 min after the open
CONDOR_VWAP_NEAR_PCT = 0.0015      # condor: price must be within this % of VWAP


def _session_vwap(bars) -> float:
    """Volume-weighted average price over the given bars (typical price)."""
    tp = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vol = bars["volume"].fillna(0)
    denom = float(vol.sum())
    if denom <= 0:
        return float(bars["close"].iloc[-1])
    return float((tp * vol).sum() / denom)


def confirm_entry(structure: str, or_high: float, or_low: float,
                  vwap: float, price: float) -> bool:
    """
    The blend confirmation: opening-range + VWAP.

      iron_condor : price holding INSIDE the opening range AND near VWAP
                    → the range is real, sell premium around it.
      bull_debit  : price ABOVE the opening-range high AND above VWAP
                    → up-breakout held, ride it.
      bear_debit  : price BELOW the opening-range low AND below VWAP
                    → down-breakout held.
    Returns False (no trade) when the setup hasn't confirmed.
    """
    if structure == "iron_condor":
        in_range = or_low <= price <= or_high
        near_vwap = abs(price - vwap) <= CONDOR_VWAP_NEAR_PCT * price
        return in_range and near_vwap
    if structure == "bull_debit":
        return price > or_high and price > vwap
    if structure == "bear_debit":
        return price < or_low and price < vwap
    return False


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
    """Construct leg specs at point-offsets from spot (SPY $1 strikes).
    Delegates to the shared builder so live + backtest select identically."""
    return _select_legs(structure, spot)


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
                      require_confirmation: bool = True,
                      profit_target_pct: float = PROFIT_TARGET_PCT,
                      stop_mult: float | None = STOP_MULT) -> dict | None:
    """
    Real-priced 0DTE simulation for one day. Entry is at the opening-range end
    (~9:45 ET) gated by the blend confirmation (opening-range + VWAP). Returns
    a realized P&L dict, or None when the day can't be priced OR the setup
    never confirms (require_confirmation=True → no trade).

    Set require_confirmation=False to reproduce the blind-entry baseline.
    """
    from datetime import datetime, timedelta
    from data.options_history import option_ticker
    import pandas as pd

    if spy_intraday is None or spy_intraday.empty:
        return None
    spy = _to_et(spy_intraday)
    rth = spy[(spy.index.time >= MARKET_OPEN_ET) & (spy.index.time <= EOD_FLATTEN_ET)]
    if rth.empty:
        return None

    # Opening range = first OR_MINUTES after the open; entry at OR end.
    or_end = (datetime.combine(day, MARKET_OPEN_ET) + timedelta(minutes=OR_MINUTES)).time()
    or_bars = rth[rth.index.time < or_end]
    session = rth[rth.index.time >= or_end]
    if or_bars.empty or session.empty:
        return None

    entry_ts   = session.index[0]
    entry_spot = float(session.iloc[0]["close"])
    or_high    = float(or_bars["high"].max())
    or_low     = float(or_bars["low"].min())
    vwap       = _session_vwap(rth[rth.index <= entry_ts])

    if require_confirmation and not confirm_entry(structure, or_high, or_low, vwap, entry_spot):
        return None   # setup didn't confirm → no trade today

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

    # ── Entry structure: raw mark from shared builder ──────────────────────
    # build_structure(entry_ts=entry_ts, expiry=day) matches this function's
    # semantics exactly: 0DTE expiry == day, mark at entry_ts (opening-range end).
    from signals.intraday_structure_builder import build_structure, HistoricalPricer
    _built = build_structure(structure, "0DTE", entry_spot,
                             HistoricalPricer(options_history),
                             as_of=day, entry_ts=entry_ts, expiry=day)
    if _built is None:
        return None
    # Apply slippage and compute max_profit using the same formula as before.
    # (The builder returns the raw spread value; slippage is a backtest concern.)
    credit = is_credit_structure(structure)
    entry_px = _built["entry_price"]
    entry_px = (entry_px - SLIPPAGE) if credit else (entry_px + SLIPPAGE)
    if entry_px <= 0:
        return None
    width      = abs(legs[0]["strike"] - legs[1]["strike"]) if len(legs) >= 2 else 0
    max_profit = entry_px * 100 if credit else (width - entry_px) * 100
    # max_loss is the mirror of max_profit (same width/entry_px the sim uses).
    # debit  : risk == the premium paid  -> entry_px*100
    # credit : risk == width minus credit -> (width-entry_px)*100
    max_loss   = (width - entry_px) * 100 if credit else entry_px * 100
    n_legs     = len(legs)
    commission = COMMISSION_PER_LEG * n_legs * 2

    # Walk the session, mark the spread, exit on target / stop / EOD.
    # Also record the full per-bar PnL path with BOTH the real-option-bar mark
    # and a Black-Scholes-off-the-intraday-spot mark (the BS mark mirrors what
    # the live ExitManager sees — used later for an arm-replay parity check).
    from learning.exit_manager import bs_price
    # VIX proxy: the backtest has no live VIX; use a fixed sigma stand-in matching
    # the live BS convention (sigma = vix/100). 0.15 ~ VIX 15, the calm regime
    # these intraday trades fire in. Documented approximation.
    BS_SIGMA = 0.15

    def _bs_spread_mark(spot_now, legs, structure):
        long_v = short_v = 0.0
        for leg in legs:
            otype = "call" if str(leg["cp"]).lower().startswith("c") else "put"
            p = bs_price(otype, spot_now, leg["strike"], 0.5 / 365.0, BS_SIGMA)
            if leg["action"] == "BUY":
                long_v += p
            else:
                short_v += p
        return max(0.0, (short_v - long_v) if credit else (long_v - short_v))

    exit_reason = "eod"
    pnl = -commission
    path = []
    for ts in session.index:
        m = marks_at(ts)
        if m is None:
            continue
        val = _spread_value(m, structure)
        if credit:
            pnl = (entry_px - (val + SLIPPAGE)) * 100 - commission
            exit_px_bar = round(val + SLIPPAGE, 2)
        else:
            pnl = (max(0.0, val - SLIPPAGE) - entry_px) * 100 - commission
            exit_px_bar = round(max(0.0, val - SLIPPAGE), 2)

        spot_now = float(spy.loc[ts]["close"]) if ts in spy.index else entry_spot
        val_bs = _bs_spread_mark(spot_now, legs, structure)
        if credit:
            pnl_bs = (entry_px - (val_bs + SLIPPAGE)) * 100 - commission
            exit_px_bs = round(val_bs + SLIPPAGE, 2)
        else:
            pnl_bs = (max(0.0, val_bs - SLIPPAGE) - entry_px) * 100 - commission
            exit_px_bs = round(max(0.0, val_bs - SLIPPAGE), 2)

        path.append({"t": ts.strftime("%H:%M"), "pnl": round(pnl, 2),
                     "exit_price": exit_px_bar, "pnl_bs": round(pnl_bs, 2),
                     "exit_price_bs": exit_px_bs})

        if max_profit > 0 and pnl >= profit_target_pct * max_profit:
            exit_reason = "target"; break
        if stop_mult is not None and pnl <= -stop_mult * max_profit:
            exit_reason = "stop"; break

    return {
        "date": day.isoformat(), "structure": structure,
        "entry_spot": round(entry_spot, 2), "entry_px": round(entry_px, 2),
        "max_profit": round(max_profit, 2), "max_loss": round(max_loss, 2),
        "pnl_dollars": round(pnl, 2),
        "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "exit_reason": exit_reason,
        "path": path,
        "pnl_hold": path[-1]["pnl"] if path else round(pnl, 2),
    }


def run_intraday_backtest(from_date: date, to_date: date,
                          event_dates: set | None = None,
                          require_confirmation: bool = True,
                          profit_target_pct: float = PROFIT_TARGET_PCT,
                          stop_mult: float | None = STOP_MULT) -> "pd.DataFrame":
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
        res = simulate_0dte_day(d, structure, spy_intraday, oh,
                                require_confirmation=require_confirmation,
                                profit_target_pct=profit_target_pct,
                                stop_mult=stop_mult)
        if res:
            res["regime"] = regime
            rows.append(res)
    return pd.DataFrame(rows)
