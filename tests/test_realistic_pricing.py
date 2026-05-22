"""
tests/test_realistic_pricing.py -- option-priced backtest engine.

Verifies the BS-priced spread P&L has correct directionality and that the
concurrency cap actually limits overlapping positions.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.realistic_pricing import (
    build_legs, _net_value, _spread_width, simulate_trade,
    run_realistic_backtest, _is_credit,
)


def test_build_legs_structures():
    legs = build_legs(550.0, "iron_condor")
    assert len(legs) == 4
    assert sum(1 for l in legs if l["action"] == "SELL") == 2
    assert _spread_width(legs) > 0
    assert len(build_legs(550.0, "bull_debit")) == 2
    assert build_legs(550.0, "nonsense") == []


def test_bull_debit_profits_on_up_move():
    legs = build_legs(550.0, "bull_debit")
    l0, s0 = _net_value(legs, 550.0, 16.0, 45); debit = l0 - s0
    up,  _ = _net_value(legs, 565.0, 16.0, 21)
    upS    = up - _
    dn,  _ = _net_value(legs, 535.0, 16.0, 21)
    dnS    = dn - _
    assert upS - debit > 0     # SPY up → bull debit gains
    assert dnS - debit < 0     # SPY down → bull debit loses


def test_bear_debit_profits_on_down_move():
    legs = build_legs(550.0, "bear_debit")
    l0, s0 = _net_value(legs, 550.0, 16.0, 45); debit = l0 - s0
    up = _net_value(legs, 565.0, 16.0, 21); upS = up[0] - up[1]
    dn = _net_value(legs, 535.0, 16.0, 21); dnS = dn[0] - dn[1]
    assert dnS - debit > 0     # SPY down → bear debit gains
    assert upS - debit < 0     # SPY up → bear debit loses


def test_is_credit_classification():
    assert _is_credit("iron_condor") and _is_credit("bull_credit")
    assert not _is_credit("bull_debit") and not _is_credit("bear_debit")


def _ramp_df(n=120, start=500.0, step=1.0):
    """Steadily rising SPY series with dates."""
    d0 = date(2025, 1, 1)
    dates = [pd.Timestamp(d0 + timedelta(days=i)) for i in range(n)]
    closes = [start + step * i for i in range(n)]
    return pd.DataFrame({"close": closes}, index=dates)


def test_simulate_trade_returns_pnl_and_respects_directionality():
    df = _ramp_df()
    dates = list(df.index)
    r = simulate_trade(df, dates, 0, "bull_debit", {})
    assert r is not None
    assert r["pnl_dollars"] > 0          # rising market → bull debit wins
    assert r["exit_reason"] in ("target", "time_stop", "expiry")
    assert r["days_held"] > 0


def test_stop_loss_frac_caps_a_losing_condor():
    """A steep up-ramp drives the condor's short call ITM → a loss. A tight
    stop should fire with exit_reason 'stop' and cut the loss vs holding."""
    df = _ramp_df(n=120, start=500.0, step=4.0)   # +4/day = strong uptrend
    dates = list(df.index)
    held    = simulate_trade(df, dates, 0, "iron_condor", {}, stop_loss_frac=None)
    stopped = simulate_trade(df, dates, 0, "iron_condor", {}, stop_loss_frac=0.3)
    assert held is not None and stopped is not None
    assert held["pnl_dollars"] < 0                 # the condor loses on this ramp
    assert stopped["exit_reason"] == "stop"        # the stop actually fired
    assert stopped["days_held"] <= held["days_held"]
    assert stopped["pnl_dollars"] >= held["pnl_dollars"]   # loss was capped


def test_stop_loss_frac_none_is_unchanged():
    """Default (no stop) must price identically to passing frac explicitly None."""
    df = _ramp_df(n=120, start=500.0, step=4.0)
    dates = list(df.index)
    a = simulate_trade(df, dates, 0, "iron_condor", {})
    b = simulate_trade(df, dates, 0, "iron_condor", {}, stop_loss_frac=None)
    assert a["pnl_dollars"] == b["pnl_dollars"]
    assert "stop" not in (a["exit_reason"], b["exit_reason"])


def test_concurrency_cap_limits_overlap():
    """A signal every day on a 45-DTE hold should open far fewer trades at
    max_concurrent=1 than unconstrained."""
    df = _ramp_df(n=120)
    # Fabricate a 'tradeable bull_debit every day' regime frame.
    regime = pd.DataFrame([
        {"date": d, "play": "bull_debit", "tradeable": True} for d in df.index
    ])
    one  = run_realistic_backtest(df, regime, max_concurrent=1)
    many = run_realistic_backtest(df, regime, max_concurrent=99)
    assert len(one) < len(many)
    # With ~24-day holds, a single-position account opens roughly
    # n_days / hold trades — far fewer than one-per-day.
    assert len(one) <= len(df) / 10
