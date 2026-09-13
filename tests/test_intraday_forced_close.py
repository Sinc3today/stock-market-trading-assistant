"""tests/test_intraday_forced_close.py -- a same-day trade must be allowed to live.

Every 0DTE trade in the journal -- 34 in the learning sandbox, 2 in the
disciplined book, and the 4 opened AFTER the 2026-09-06 intrinsic-mark fix --
was closed exactly five minutes after entry, at the intraday exit cron's first
check. Stops, targets and "time stops" alike.

The cause is one line in ExitManager._evaluate:

    if dte <= rule["dte_close_threshold"]:      # threshold is 0 for 0DTE

On expiry day dte is 0, so 0 <= 0 is true from the first check. The learning
sandbox has therefore never tested a same-day strategy at all; it has tested
five-minute holds, and its record (and the reflector's conclusions drawn from
it) describe that instead.

1-3DTE had the milder version: on expiry day the same line closes the position
at the cron's first fire, 09:00 ET, before the market opens, at a model mark.

Intended behaviour existed in config as FORCED_CLOSE_TIME_0DTE_* and
FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE, which nothing read; they were
deleted as phantom features on 2026-09-08 instead of being wired. Restored now,
with the 0DTE time taken from the only behaviour ever MEASURED -- the intraday
backtest's 15:45 ET flatten -- rather than the never-tested 15:30/15:00 values.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta

import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.exit_manager import ExitManager, exit_rule_for

ET = pytz.timezone("US/Eastern")
DAY = date(2026, 9, 16)


def _at(hhmm: str, day: date = DAY) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return ET.localize(datetime(day.year, day.month, day.day, h, m))


def _trade(strategy="call_debit_spread", bucket="0DTE", expiry=DAY):
    legs = ([{"action": "BUY", "option_type": "CALL", "strike": 700, "expiry": expiry.isoformat()},
             {"action": "SELL", "option_type": "CALL", "strike": 705, "expiry": expiry.isoformat()}]
            if strategy != "iron_condor" else
            [{"action": "SELL", "option_type": "CALL", "strike": 710, "expiry": expiry.isoformat()},
             {"action": "BUY", "option_type": "CALL", "strike": 715, "expiry": expiry.isoformat()},
             {"action": "SELL", "option_type": "PUT", "strike": 690, "expiry": expiry.isoformat()},
             {"action": "BUY", "option_type": "PUT", "strike": 685, "expiry": expiry.isoformat()}])
    return {"trade_id": "T1", "strategy": strategy, "entry_price": 1.00, "size": 1,
            "max_profit": 400.0, "max_loss": 100.0, "legs": legs,
            "dte_bucket": bucket, "book": "learning"}


@pytest.fixture
def flat_mgr(monkeypatch):
    """Mark every position exactly at its entry price, so neither the profit
    target nor the stop can fire and ONLY the time rule decides."""
    mgr = ExitManager()
    monkeypatch.setattr(mgr, "_mark_exit_price",
                        lambda strategy, legs, spy, vix, today, dte, now=None: 1.00)
    return mgr


# ── the defect ────────────────────────────────────────────────────

@pytest.mark.parametrize("strategy", ["call_debit_spread", "put_debit_spread", "iron_condor"])
def test_a_0dte_position_is_not_closed_at_its_first_check(flat_mgr, strategy):
    t = _trade(strategy)
    assert flat_mgr._evaluate(t, 700.0, 16.0, DAY, now=_at("09:50")) is None


def test_a_1_3dte_position_is_not_closed_before_the_open_on_expiry_day(flat_mgr):
    t = _trade(bucket="1-3DTE", expiry=DAY)
    assert flat_mgr._evaluate(t, 700.0, 16.0, DAY, now=_at("09:05")) is None


# ── the intended close ────────────────────────────────────────────

@pytest.mark.parametrize("strategy", ["call_debit_spread", "iron_condor"])
def test_a_0dte_position_is_flattened_at_the_forced_close(flat_mgr, strategy):
    d = flat_mgr._evaluate(_trade(strategy), 700.0, 16.0, DAY, now=_at("15:45"))
    assert d is not None and "forced close" in d[1]


def test_a_0dte_position_just_before_the_forced_close_stays_open(flat_mgr):
    assert flat_mgr._evaluate(_trade(), 700.0, 16.0, DAY, now=_at("15:40")) is None


def test_a_1_3dte_position_closes_thirty_minutes_before_expiry(flat_mgr):
    t = _trade(bucket="1-3DTE", expiry=DAY)
    d = flat_mgr._evaluate(t, 700.0, 16.0, DAY, now=_at("15:30"))
    assert d is not None and "forced close" in d[1]


def test_a_1_3dte_position_is_held_overnight_before_its_expiry_day(flat_mgr):
    t = _trade(bucket="1-3DTE", expiry=DAY + timedelta(days=2))
    assert flat_mgr._evaluate(t, 700.0, 16.0, DAY, now=_at("15:55")) is None


def test_a_naive_now_is_read_as_eastern_not_host_local(flat_mgr):
    """The host is in Chicago. 15:45 means 15:45 in New York."""
    naive = datetime(2026, 9, 16, 15, 45)
    assert flat_mgr._evaluate(_trade(), 700.0, 16.0, DAY, now=naive) is not None


# ── nothing else moved ────────────────────────────────────────────

def test_a_profit_target_still_fires_early_in_the_day(monkeypatch):
    mgr = ExitManager()
    monkeypatch.setattr(mgr, "_mark_exit_price",
                        lambda strategy, legs, spy, vix, today, dte, now=None: 5.00)
    d = mgr._evaluate(_trade(), 700.0, 16.0, DAY, now=_at("09:50"))
    assert d is not None and "profit target" in d[1]


def test_the_45dte_time_stop_is_unchanged(flat_mgr):
    t = _trade(strategy="debit_spread", bucket="45DTE", expiry=DAY + timedelta(days=21))
    d = flat_mgr._evaluate(t, 700.0, 16.0, DAY, now=_at("09:50"))
    assert d is not None and "time stop 21DTE" in d[1]


def test_live_forced_close_matches_the_backtest_flatten():
    """Live and backtest disagreeing about when a trade ends is how the
    sandbox came to measure something no study ever tested."""
    from backtests.intraday_backtest import EOD_FLATTEN_ET
    h, m = (int(x) for x in exit_rule_for("call_debit_spread", "0DTE")["forced_close_time"].split(":"))
    assert time(h, m) == EOD_FLATTEN_ET


def test_the_forced_close_settings_come_from_config():
    assert exit_rule_for("iron_condor", "0DTE")["forced_close_time"] == config.FORCED_CLOSE_TIME_0DTE
    assert (exit_rule_for("put_debit_spread", "1-3DTE")["forced_close_minutes_before_expiry"]
            == config.FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE)
