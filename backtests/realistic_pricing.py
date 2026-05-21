"""
backtests/realistic_pricing.py -- Option-priced backtest P&L.

The legacy backtest (spy_daily_backtest.py) maps each outcome to a FIXED
dollar amount (condor win = +$130, etc.) decided by SPY's 5-day move. That
is fine for *ranking* configurations but is not a realistic P&L: it ignores
the actual credit/debit, time decay, how the position is managed, commissions
and slippage.

This module prices the real trade lifecycle:

  1. Build the spread's legs at % offsets from spot (mirrors the live
     structures: iron condor, bull/bear debit, bull/bear credit).
  2. Price entry with Black-Scholes (VIX/100 as IV, ~45 DTE) -- reusing
     learning.exit_manager.bs_price, the same engine the live paper trader
     marks with.
  3. Walk the position forward day by day, re-marking with BS, and close on
     the live exit rules (PROFIT_TARGET_PCT of max profit, or DTE_CLOSE_
     THRESHOLD days to expiry, or expiry intrinsic). No hard stop.
  4. Realized P&L = entry vs exit, x100, minus per-leg commissions and
     slippage applied in our disfavor.

This is the honest foundation the walk-forward harness + any ML learner will
sit on. It still uses BS (not historical real chains, which the free Polygon
tier lacks), so it's a *model*, not ground truth -- but a far more realistic
one than fixed payoffs.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

from learning.exit_manager import (
    bs_price, PROFIT_TARGET_PCT, DTE_CLOSE_THRESHOLD, EXIT_SLIPPAGE,
)

# ── Trade construction + frictions ───────────────────────────────────
ENTRY_DTE          = 45      # calendar days to expiry at entry (swing)
SHORT_OFFSET_PCT   = 0.025   # short strike this far OTM
WING_PCT           = 0.020   # long strike this much beyond the short (wing)
DEBIT_LONG_ATM     = 0.0     # debit long leg at-the-money
DEBIT_SHORT_OFFSET = 0.025   # debit short leg this far OTM
COMMISSION_PER_LEG = 0.65    # per contract per leg, each way (open + close)
ENTRY_SLIPPAGE     = EXIT_SLIPPAGE  # same per-share haircut on the way in


def build_legs(spot: float, play: str) -> list[dict]:
    """Construct the spread's legs at fixed % offsets from spot. Returns a
    list of {action, type, strike}. Mirrors the live structures."""
    def k(mult): return round(spot * mult, 2)
    if play == "iron_condor":
        return [
            {"action": "SELL", "type": "put",  "strike": k(1 - SHORT_OFFSET_PCT)},
            {"action": "BUY",  "type": "put",  "strike": k(1 - SHORT_OFFSET_PCT - WING_PCT)},
            {"action": "SELL", "type": "call", "strike": k(1 + SHORT_OFFSET_PCT)},
            {"action": "BUY",  "type": "call", "strike": k(1 + SHORT_OFFSET_PCT + WING_PCT)},
        ]
    if play == "bull_debit":
        return [
            {"action": "BUY",  "type": "call", "strike": k(1 + DEBIT_LONG_ATM)},
            {"action": "SELL", "type": "call", "strike": k(1 + DEBIT_SHORT_OFFSET)},
        ]
    if play == "bear_debit":
        return [
            {"action": "BUY",  "type": "put",  "strike": k(1 - DEBIT_LONG_ATM)},
            {"action": "SELL", "type": "put",  "strike": k(1 - DEBIT_SHORT_OFFSET)},
        ]
    if play == "bull_credit":   # bull put credit spread
        return [
            {"action": "SELL", "type": "put",  "strike": k(1 - SHORT_OFFSET_PCT)},
            {"action": "BUY",  "type": "put",  "strike": k(1 - SHORT_OFFSET_PCT - WING_PCT)},
        ]
    if play == "bear_credit":   # bear call credit spread
        return [
            {"action": "SELL", "type": "call", "strike": k(1 + SHORT_OFFSET_PCT)},
            {"action": "BUY",  "type": "call", "strike": k(1 + SHORT_OFFSET_PCT + WING_PCT)},
        ]
    return []


def _net_value(legs, spot, vix, dte_days):
    """BS value of long legs minus short legs (signed, per share)."""
    sigma = vix / 100.0
    t = max(dte_days, 0) / 365.0
    long_v = short_v = 0.0
    for leg in legs:
        p = bs_price(leg["type"], spot, leg["strike"], t, sigma)
        if leg["action"] == "BUY":
            long_v += p
        else:
            short_v += p
    return long_v, short_v


def _is_credit(play: str) -> bool:
    return play in ("iron_condor", "bull_credit", "bear_credit")


def _spread_width(legs: list[dict]) -> float:
    """Strike width of the (widest) vertical — used for max profit/loss."""
    puts  = sorted([l["strike"] for l in legs if l["type"] == "put"])
    calls = sorted([l["strike"] for l in legs if l["type"] == "call"])
    widths = []
    if len(puts) >= 2:  widths.append(abs(puts[1]  - puts[0]))
    if len(calls) >= 2: widths.append(abs(calls[1] - calls[0]))
    return max(widths) if widths else 0.0


def simulate_trade(spy_df: pd.DataFrame, dates: list, entry_idx: int,
                   play: str, vix_at: dict) -> dict | None:
    """
    Price one trade's full lifecycle realistically. Returns a dict with
    realized pnl_dollars, outcome, exit_reason, days_held -- or None if the
    play isn't a recognised structure.

    vix_at: mapping date -> vix (falls back to 16.0).
    """
    legs = build_legs(float(spy_df.loc[dates[entry_idx], "close"]), play)
    if not legs:
        return None
    entry_date = dates[entry_idx]
    expiry     = entry_date + timedelta(days=ENTRY_DTE)
    spot0      = float(spy_df.loc[entry_date, "close"])
    vix0       = vix_at.get(entry_date, 16.0)
    credit     = _is_credit(play)
    width      = _spread_width(legs)

    long0, short0 = _net_value(legs, spot0, vix0, ENTRY_DTE)
    if credit:
        entry_px   = max(0.0, short0 - long0) - ENTRY_SLIPPAGE   # credit received (less, slipped)
        max_profit = entry_px * 100
        max_loss   = (width - entry_px) * 100
    else:
        entry_px   = max(0.0, long0 - short0) + ENTRY_SLIPPAGE   # debit paid (more, slipped)
        max_profit = (width - entry_px) * 100
        max_loss   = entry_px * 100

    n_legs   = len(legs)
    commission = COMMISSION_PER_LEG * n_legs * 2   # open + close

    # Walk forward until an exit rule fires or we run out of data.
    for j in range(entry_idx + 1, len(dates)):
        d   = dates[j]
        dte = (expiry - d).days
        spot = float(spy_df.loc[d, "close"])
        vix  = vix_at.get(d, vix0)
        long_v, short_v = _net_value(legs, spot, vix, max(dte, 0))
        if credit:
            cost = max(0.0, short_v - long_v) + EXIT_SLIPPAGE     # pay to close (slipped worse)
            pnl  = (entry_px - cost) * 100
        else:
            proceeds = max(0.0, long_v - short_v) - EXIT_SLIPPAGE  # receive to close (slipped worse)
            pnl      = (proceeds - entry_px) * 100

        hit_target = max_profit > 0 and pnl / max_profit >= PROFIT_TARGET_PCT
        if hit_target or dte <= DTE_CLOSE_THRESHOLD or dte <= 0:
            net = pnl - commission
            return {
                "play":        play,
                "pnl_dollars": round(net, 2),
                "outcome":     "win" if net > 0 else "loss" if net < 0 else "breakeven",
                "exit_reason": "target" if hit_target else ("expiry" if dte <= 0 else "time_stop"),
                "days_held":   (d - entry_date).days,
                "entry_px":    round(entry_px, 2),
            }
    return None  # ran off the end of the data


def realistic_pnl_for_play(play: str, tradeable: bool) -> bool:
    return tradeable and play in (
        "iron_condor", "bull_debit", "bear_debit", "bull_credit", "bear_credit"
    )


def _vix_lookup(dates, vix_df) -> dict:
    """date -> nearest-prior VIX close (16.0 fallback)."""
    out, last = {}, 16.0
    vat = {}
    if vix_df is not None:
        vx = vix_df.copy(); vx.index = pd.to_datetime(vx.index)
        vat = {d: float(vx.loc[d, "close"]) for d in vx.index}
    for d in dates:
        if d in vat:
            last = vat[d]
        out[d] = last
    return out


def run_realistic_backtest(spy_df, regime_results: pd.DataFrame, vix_df=None,
                           max_concurrent: int = 1) -> pd.DataFrame:
    """
    Replay realistic per-trade P&L under a position-CONCURRENCY limit.

    regime_results: the DataFrame from SPYBacktest.run() (date/play/tradeable).
    max_concurrent: how many positions may be open at once. 1 = a single-
    position account (what one busy human can actually follow); a large
    number approximates the unconstrained "take every signal" backtest.

    Concurrency matters enormously: the unconstrained backtest opens a fresh
    45-DTE trade EVERY tradeable day, stacking 10+ overlapping positions on
    the same market move. That inflates totals to fantasy levels. Capping
    concurrency is what makes the P&L reflect a real account.
    """
    from datetime import timedelta
    spy_df = spy_df.copy(); spy_df.index = pd.to_datetime(spy_df.index)
    dates  = sorted(pd.to_datetime(spy_df.index))
    didx   = {d: i for i, d in enumerate(dates)}
    va     = _vix_lookup(dates, vix_df)

    signals = {
        pd.to_datetime(r["date"]): r["play"]
        for _, r in regime_results[regime_results["tradeable"] == True].iterrows()
        if r["play"] != "skip"
    }

    open_until: list = []
    rows: list[dict] = []
    for d in dates:
        open_until = [x for x in open_until if x > d]
        if d in signals and len(open_until) < max_concurrent:
            r = simulate_trade(spy_df, dates, didx[d], signals[d], va)
            if r:
                r["date"] = d
                rows.append(r)
                open_until.append(d + timedelta(days=r["days_held"]))
    return pd.DataFrame(rows)
