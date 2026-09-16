"""tests/test_intraday_rescore.py -- what did the intraday sandbox actually do?

52 closed intraday records exist, and not one of them measured what it claims:

  * Entry prices came from last trades struck at different moments — a median
    32% away from the market, worst 175% (3A87F4C4: $2.01 recorded, $0.73 real).
  * Every same-day trade was closed five minutes after entry by a time stop
    that was true from the first check, so the record describes five-minute
    holds rather than a same-day strategy.
  * 18 of them were "stopped at 75% of max loss" while the real spread was
    anywhere from 35% down to 40% UP.

So the sandbox's win rate, its P&L, and every conclusion the nightly reflection
drew from them are about something that did not happen.

This re-scores them by replaying each trade against real per-contract minute
bars under the exit rules the strategy was SUPPOSED to follow: the profit
target and stop from exit_rule_for(), checked every five minutes on the clock
like the live cron, and the forced close restored on 2026-09-13.

It is a DRY RUN. It writes nothing to the journal. Whether any of it is written
back is a separate decision, because a replay is a reconstruction — accurate,
but not the same thing as a record of what happened.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from journal.trade_recorder import round_trip_commission
from learning import intraday_rescore as ir

ET = pytz.timezone("US/Eastern")
DAY = date(2026, 9, 14)


# ── fixtures ──────────────────────────────────────────────────────

def _series(day, points: dict[str, float]):
    """Signed structure value at every session minute, stepping to each point.
    A sold structure prices NEGATIVE (you receive it), as structure_minutes does.
    """
    idx = pd.date_range(f"{day.isoformat()} 09:30", f"{day.isoformat()} 16:00",
                        freq="1min", tz="US/Eastern")
    ordered = sorted(points.items())
    vals, cur = [], None
    for ts in idx:
        hhmm = ts.strftime("%H:%M")
        for k, v in ordered:
            if hhmm >= k:
                cur = v
        vals.append(cur)
    return pd.Series(vals, index=idx, dtype="float64")


class _History:
    """structure_minutes double: one series per day, or None for 'no bars'."""
    def __init__(self, by_day):
        self.by_day = by_day
        self.days_asked = []

    def structure_minutes(self, underlying, day, legs, use_cache=True):
        self.days_asked.append(day)
        pts = self.by_day.get(day)
        idx = pd.date_range(f"{day.isoformat()} 09:30", f"{day.isoformat()} 16:00",
                            freq="1min", tz="US/Eastern")
        if pts is None:
            return pd.Series([float("nan")] * len(idx), index=idx, dtype="float64")
        return _series(day, pts)


def _debit_legs(expiry=DAY):
    return [{"action": "BUY", "option_type": "put", "strike": 759.0,
             "expiration": expiry.isoformat()},
            {"action": "SELL", "option_type": "put", "strike": 756.0,
             "expiration": expiry.isoformat()}]


def _condor_legs(expiry=DAY):
    return [{"action": "SELL", "option_type": "put", "strike": 755.0, "expiration": expiry.isoformat()},
            {"action": "BUY", "option_type": "put", "strike": 750.0, "expiration": expiry.isoformat()},
            {"action": "SELL", "option_type": "call", "strike": 765.0, "expiration": expiry.isoformat()},
            {"action": "BUY", "option_type": "call", "strike": 770.0, "expiration": expiry.isoformat()}]


def _trade(entry_price=0.26, *, bucket="0DTE", strategy="put_debit_spread",
           legs=None, hhmm="09:45 AM", day=DAY, exit_price=0.03,
           outcome="loss", pnl=-23.0, book="learning"):
    return {"trade_id": "T1", "book": book, "strategy": strategy,
            "dte_bucket": bucket, "size": 1, "entry_price": entry_price,
            "exit_price": exit_price, "outcome": outcome, "pnl_dollars": pnl,
            "entry_date": f"{day.isoformat()} {hhmm} EST",
            "exit_date": f"{day.isoformat()} 09:50 AM EST",
            "legs": legs if legs is not None else _debit_legs(day)}


# ── geometry and risk ─────────────────────────────────────────────

def test_a_debit_spread_is_three_wide():
    assert ir.structure_width(_debit_legs()) == pytest.approx(3.0)


def test_a_condor_width_is_its_wider_wing():
    assert ir.structure_width(_condor_legs()) == pytest.approx(5.0)


def test_debit_risk_is_rebuilt_from_the_real_entry():
    """The recorded max_profit/max_loss were derived from the wrong entry, so
    replaying against them would just re-import the original error."""
    mp, ml = ir.risk_from_entry("put_debit_spread", _debit_legs(), 0.68)
    assert mp == pytest.approx(232.0)      # (3.00 - 0.68) x 100
    assert ml == pytest.approx(68.0)       # the debit paid


def test_credit_risk_is_rebuilt_from_the_real_credit():
    mp, ml = ir.risk_from_entry("iron_condor", _condor_legs(), 0.53)
    assert mp == pytest.approx(53.0)
    assert ml == pytest.approx(447.0)      # (5.00 - 0.53) x 100


# ── the replay ────────────────────────────────────────────────────

def _replay(trade, by_day):
    return ir.replay_trade(trade, history=_History(by_day))


def test_entry_is_priced_from_the_market_not_from_the_record():
    r = _replay(_trade(entry_price=0.26), {DAY: {"09:30": 0.68}})
    assert r["replayed"]["entry"] == pytest.approx(0.68)
    assert r["entry_error_pct"] == pytest.approx(-61.8, abs=0.5)


def test_a_profit_target_closes_the_trade():
    """1-3DTE debit: target is 50% of max profit. Entry 1.00 on a 3-wide gives
    max profit $200, so the target is hit once the spread is worth 2.00."""
    t = _trade(bucket="1-3DTE", day=DAY, legs=_debit_legs(DAY + timedelta(days=2)))
    r = _replay(t, {DAY: {"09:30": 1.00, "11:00": 2.10}})
    assert "profit target" in r["replayed"]["reason"]
    assert r["replayed"]["exit_ts"].endswith("11:00:00-04:00")


def test_a_stop_closes_the_trade():
    """1-3DTE debit: stop at 50% of max loss (the debit), so value <= 0.50."""
    t = _trade(bucket="1-3DTE", day=DAY, legs=_debit_legs(DAY + timedelta(days=2)))
    r = _replay(t, {DAY: {"09:30": 1.00, "10:20": 0.40}})
    assert "stop" in r["replayed"]["reason"]


def test_a_same_day_trade_that_hits_nothing_is_flattened_at_the_forced_close():
    r = _replay(_trade(entry_price=0.26), {DAY: {"09:30": 0.68}})
    assert "forced close" in r["replayed"]["reason"]
    assert r["replayed"]["exit_ts"].endswith("15:45:00-04:00")


def test_checks_happen_every_five_minutes_like_the_live_cron():
    """A spike between two five-minute marks is not caught — the live exit
    manager only looks every five minutes, and the replay must not be luckier
    than the system it is measuring."""
    t = _trade(bucket="1-3DTE", day=DAY, legs=_debit_legs(DAY + timedelta(days=2)))
    r = _replay(t, {DAY: {"09:30": 1.00, "10:02": 2.50, "10:04": 1.00}})
    assert "profit target" not in r["replayed"]["reason"]


def test_a_one_to_three_day_trade_runs_to_its_expiry_day():
    exp = DAY + timedelta(days=2)
    t = _trade(bucket="1-3DTE", day=DAY, legs=_debit_legs(exp))
    r = _replay(t, {DAY: {"09:30": 1.00}, DAY + timedelta(days=1): {"09:30": 1.00},
                    exp: {"09:30": 1.00}})
    assert r["replayed"]["exit_ts"].startswith(exp.isoformat())
    assert "forced close" in r["replayed"]["reason"]


def test_a_minute_with_no_price_is_skipped_rather_than_guessed():
    t = _trade(bucket="1-3DTE", day=DAY, legs=_debit_legs(DAY + timedelta(days=2)))
    hist = _History({DAY: {"09:30": 1.00}})
    s = hist.structure_minutes("SPY", DAY, [])
    s.loc[s.index.strftime("%H:%M") < "11:00"] = float("nan")
    hist.structure_minutes = lambda *a, **k: s
    r = ir.replay_trade(t, history=hist)
    assert r["replayed"]["entry"] is None or r["status"] == ir.UNPRICED


def test_a_structure_with_no_bars_is_reported_unpriced_not_scored():
    r = _replay(_trade(), {})
    assert r["status"] == ir.UNPRICED
    assert r["replayed"].get("pnl_net") is None


# ── money ─────────────────────────────────────────────────────────

def test_pnl_pays_the_haircut_both_ways_and_commissions():
    t = _trade(bucket="1-3DTE", day=DAY, legs=_debit_legs(DAY + timedelta(days=2)))
    r = _replay(t, {DAY: {"09:30": 0.68, "11:00": 2.10}})
    h = config.REPLAY_HAIRCUT_PER_LEG * 2
    gross = ((2.10 - h) - (0.68 + h)) * 100
    fee = round_trip_commission("put_debit_spread", t["legs"], 1)
    assert r["replayed"]["pnl_gross"] == pytest.approx(gross, abs=0.01)
    assert r["replayed"]["pnl_net"] == pytest.approx(gross - fee, abs=0.01)


def test_a_sold_structure_uses_the_credit_convention():
    """structure_minutes returns NEGATIVE for a sold structure. Profit is the
    credit kept, so a condor sold for 0.90 and bought back at 0.10 wins.

    The credit has to be big enough to clear the condor's own costs: four legs
    means $0.20 of haircut each way plus $5.20 of commission, so a 0.53 credit
    bought back at 0.26 LOSES money however the convention is applied. That is
    a real property of trading four legs, not a quirk of this replay."""
    t = _trade(strategy="iron_condor", legs=_condor_legs(), entry_price=0.70)
    r = _replay(t, {DAY: {"09:30": -0.90, "11:00": -0.10}})
    assert r["replayed"]["entry"] == pytest.approx(0.90)
    assert r["replayed"]["pnl_net"] > 0


# ── safety and scope ──────────────────────────────────────────────

def test_the_dry_run_writes_nothing_to_the_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    journal = tmp_path / "trades.json"
    payload = [_trade()]
    journal.write_text(json.dumps(payload))
    before = journal.read_text()
    ir.plan(payload, history=_History({DAY: {"09:30": 0.68}}))
    assert journal.read_text() == before


def test_only_intraday_buckets_are_replayed():
    hist = _History({DAY: {"09:30": 0.68}})
    ir.plan([_trade(bucket="45DTE")], history=hist)
    assert hist.days_asked == []


# ── the comparison ────────────────────────────────────────────────

def test_the_summary_puts_recorded_and_replayed_side_by_side():
    rows = ir.plan([_trade()], history=_History({DAY: {"09:30": 0.68}}))
    s = ir.summarise(rows)[0]
    for k in ("book", "dte_bucket", "n", "recorded_win_pct", "replayed_win_pct",
              "recorded_net", "replayed_net", "unpriced"):
        assert k in s, k


def test_unpriced_records_are_counted_but_never_scored():
    rows = ir.plan([_trade()], history=_History({}))
    s = ir.summarise(rows)[0]
    assert s["unpriced"] == 1 and s["n"] == 0


def test_the_summary_carries_confidence_intervals_for_both():
    rows = ir.plan([_trade() for _ in range(5)],
                   history=_History({DAY: {"09:30": 0.68}}))
    s = ir.summarise(rows)[0]
    assert s["replayed_ci_low"] <= s["replayed_win_pct"] <= s["replayed_ci_high"]
