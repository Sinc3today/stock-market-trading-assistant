"""tests/test_event_shadow.py -- turn every stand-down day into a data point.

On event days (FOMC, CPI, NFP, OPEX and their eves) the bot skips, and the
learning loop then grades the skip against itself: 61 knowledge-base entries
say some version of "the skip was correct", and none asks what a trade would
have made. The daily prediction on those days is literally
"neutral, confidence 0, skip".

This records what a small set of directional rules WOULD have made, priced
from real option bars after the close. No orders, no journal writes.

Rules are pre-registered: they live in code, carry a version hash, and each
day's registration row is written before the open. Event types are scored
separately (CPI did not replicate FOMC in the June event studies, so pooling
them would hide exactly the difference we need to see), and the same generic
rules run on ordinary days as a control — if "follow the first 15 minutes"
pays just as well on a quiet Tuesday, the event is not the edge.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from datetime import date, datetime

import pandas as pd
import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data import options_history as oh
from learning import event_shadow as es

ET = pytz.timezone("US/Eastern")
FOMC_DAY = date(2026, 9, 16)      # Wednesday
NEXT_DAY = date(2026, 9, 17)


def _utc(day, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return ET.localize(datetime(day.year, day.month, day.day, h, m)
                       ).astimezone(pytz.utc).replace(tzinfo=None)


def _series(day, points: dict[str, float], start="09:30", end="15:59"):
    """Minute bars (naive UTC) that step to each given price and hold it."""
    idx = pd.date_range(_utc(day, start), _utc(day, end), freq="1min", name="timestamp")
    ordered = sorted(points.items())
    px, vals = None, []
    for ts in idx:
        et = pytz.utc.localize(ts).astimezone(ET).strftime("%H:%M")
        for hhmm, p in ordered:
            if et >= hhmm:
                px = p
        vals.append(px)
    return pd.DataFrame({"open": vals, "high": vals, "low": vals, "close": vals,
                         "volume": 1}, index=idx)


class _History(oh.OptionsHistory):
    def __init__(self, bars):
        self.bars, self.calls = bars, []

    def get_aggs(self, contract, multiplier, timespan, from_date, to_date,
                 limit=50000, use_cache=True):
        self.calls.append((contract, use_cache))
        return self.bars.get(contract, pd.DataFrame(columns=oh._COLS))


def _tk(exp, cp, k):
    return oh.option_ticker("SPY", exp, cp, k)


def _market(day=FOMC_DAY, exp=NEXT_DAY, *, open_move=+1.0, decision_move=+2.0,
            presser_move=-3.0, call_path=(2.00, 2.60), put_path=(2.00, 1.40)):
    """SPY opens 700, drifts open_move by 09:44, sits at 701 into 13:59, moves
    decision_move by 14:04, and is presser_move from 13:59 by 14:34.
    Option spreads priced at entry=first value, exit(15:55)=second value."""
    spy = _series(day, {"09:30": 700.0, "09:44": 700.0 + open_move,
                        "13:59": 701.0, "14:04": 701.0 + decision_move,
                        "14:34": 701.0 + presser_move, "15:55": 701.0})
    # Price every strike on a straight line so that ANY $5 vertical is worth
    # exactly the requested spread value, whichever strike the rule picks.
    # (A first draft assigned long and short prices per strike in one loop, and
    # later strikes overwrote earlier ones.)
    ce, cx = call_path[0] / 5.0, call_path[1] / 5.0
    pe, px = put_path[0] / 5.0, put_path[1] / 5.0
    bars = {"SPY": spy}
    for k in range(680, 721):
        bars[_tk(exp, "C", k)] = _series(day, {"09:30": 1.0 + (720 - k) * ce,
                                               "15:55": 1.0 + (720 - k) * cx})
        bars[_tk(exp, "P", k)] = _series(day, {"09:30": 1.0 + (k - 680) * pe,
                                               "15:55": 1.0 + (k - 680) * px})
    return _History(bars)


# ── pre-registration ──────────────────────────────────────────────

def test_rules_version_is_stable():
    assert es.rules_version() == es.rules_version()


def test_rules_version_changes_when_a_rule_changes(monkeypatch):
    before = es.rules_version()
    changed = (dataclasses.replace(es.RULES[0], exit="15:30"),) + tuple(es.RULES[1:])
    monkeypatch.setattr(es, "RULES", changed)
    assert es.rules_version() != before


def test_every_rule_states_its_hypothesis_in_plain_words():
    for r in es.RULES:
        assert len(r.hypothesis) > 20, r.rule_id


def test_every_rule_enters_strictly_after_its_signal_and_inside_the_window():
    """No look-ahead, and no entries the live system would refuse."""
    for r in es.RULES:
        assert r.entry > r.signal_end, r.rule_id
        assert "09:45" <= r.entry <= "15:00", r.rule_id
        assert r.exit > r.entry, r.rule_id


# ── which rules run on which day ──────────────────────────────────

def test_fomc_rules_run_only_on_fomc_days():
    ids = lambda types: {r.rule_id for r in es.applicable_rules(types)}
    assert "fomc_decision_follow" in ids(["FOMC"])
    assert "fomc_decision_follow" not in ids(["CPI"])
    assert "fomc_decision_follow" not in ids(["none"])


def test_generic_rules_run_on_every_day_including_controls():
    for types in (["FOMC"], ["CPI"], ["OPEX", "OPEX_EVE"], ["none"]):
        assert "open_follow" in {r.rule_id for r in es.applicable_rules(types)}


# ── resolution ────────────────────────────────────────────────────

def _resolve(tmp_path, hist, types=("FOMC",), day=FOMC_DAY):
    path = tmp_path / "shadow.jsonl"
    rows = es.resolve_day(day, event_types=list(types), history=hist, path=str(path),
                          now=ET.localize(datetime(2026, 9, 20, 12, 0)))
    return {r["rule_id"]: r for r in rows}, path


def test_follow_buys_calls_after_an_up_move(tmp_path):
    res, _ = _resolve(tmp_path, _market(open_move=+1.0))
    r = res["open_follow"]
    assert r["direction"] == "bullish"
    assert {l["option_type"] for l in r["legs"]} == {"call"}


def test_fade_takes_the_opposite_side(tmp_path):
    res, _ = _resolve(tmp_path, _market(open_move=+1.0))
    assert res["open_fade"]["direction"] == "bearish"


def test_presser_fade_uses_the_move_since_before_the_decision(tmp_path):
    """-3 from 13:59 to 14:34 -> fade is bullish."""
    res, _ = _resolve(tmp_path, _market(presser_move=-3.0))
    assert res["fomc_presser_fade"]["direction"] == "bullish"


def test_the_trade_uses_the_next_trading_day_expiry_not_same_day(tmp_path):
    """Our own research closed same-day debit spreads: theta eats them."""
    res, _ = _resolve(tmp_path, _market())
    assert all(l["expiration"] == NEXT_DAY.isoformat()
               for l in res["open_follow"]["legs"])


def test_friday_events_expire_on_the_next_trading_day(tmp_path):
    fri, mon = date(2026, 9, 18), date(2026, 9, 21)
    res, _ = _resolve(tmp_path, _market(day=fri, exp=mon), types=["OPEX"], day=fri)
    assert res["open_follow"]["status"] == es.PRICED
    assert res["open_follow"]["legs"][0]["expiration"] == mon.isoformat()


def test_pnl_pays_the_haircut_both_ways_and_commissions(tmp_path):
    from journal.trade_recorder import round_trip_commission
    res, _ = _resolve(tmp_path, _market(open_move=+1.0, call_path=(2.00, 2.60)))
    r = res["open_follow"]
    h = es.HAIRCUT_PER_LEG * 2
    gross = ((2.60 - h) - (2.00 + h)) * 100
    assert r["pnl_gross"] == pytest.approx(gross, abs=0.01)
    assert r["pnl_net"] == pytest.approx(
        gross - round_trip_commission("debit_spread", r["legs"], 1), abs=0.01)


def test_a_missing_contract_is_unpriced_never_guessed(tmp_path):
    hist = _market()
    hist.bars = {k: v for k, v in hist.bars.items() if k == "SPY"}
    res, _ = _resolve(tmp_path, hist)
    r = res["open_follow"]
    assert r["status"] == es.UNPRICED
    assert r.get("pnl_net") is None


def test_an_impossible_spread_value_is_flagged_not_scored(tmp_path):
    """A $5-wide debit spread cannot be worth more than $5."""
    res, _ = _resolve(tmp_path, _market(call_path=(7.50, 7.60)))
    assert res["open_follow"]["status"] == es.IMPLAUSIBLE


def test_a_flat_signal_takes_no_position(tmp_path):
    res, _ = _resolve(tmp_path, _market(open_move=0.0))
    assert res["open_follow"]["status"] == es.NO_SIGNAL


def test_resolution_is_idempotent(tmp_path):
    hist = _market()
    _, path = _resolve(tmp_path, hist)
    again = es.resolve_day(FOMC_DAY, event_types=["FOMC"], history=hist, path=str(path),
                           now=ET.localize(datetime(2026, 9, 20, 12, 0)))
    assert again == []
    assert sum(1 for _ in open(path)) == len(es.applicable_rules(["FOMC"]))


def test_same_day_resolution_bypasses_the_cache(tmp_path):
    """The option cache also stores EMPTY results. A same-day fetch that runs
    before the data settles would freeze 'no bars' forever."""
    hist = _market()
    es.resolve_day(FOMC_DAY, event_types=["FOMC"], history=hist,
                   path=str(tmp_path / "s.jsonl"),
                   now=ET.localize(datetime(2026, 9, 16, 16, 50)))
    assert hist.calls and all(use_cache is False for _, use_cache in hist.calls)


def test_an_unregistered_day_is_flagged(tmp_path):
    res, _ = _resolve(tmp_path, _market())
    assert res["open_follow"]["registered"] is False


def test_a_registered_day_is_marked_registered(tmp_path):
    path = tmp_path / "s.jsonl"

    class _Cal:
        def get_next_events(self, days=14):
            return [{"date": FOMC_DAY, "type": "FOMC", "label": "FOMC"}]

    reg = es.register_day(FOMC_DAY, calendar=_Cal(), path=str(path),
                          now=ET.localize(datetime(2026, 9, 16, 9, 20)))
    assert reg["event_types"] == ["FOMC"]
    rows = es.resolve_day(FOMC_DAY, history=_market(), path=str(path),
                          now=ET.localize(datetime(2026, 9, 20, 12, 0)))
    assert rows and all(r["registered"] and r["event_types"] == ["FOMC"] for r in rows)


def test_an_ordinary_day_registers_as_a_control(tmp_path):
    class _Cal:
        def get_next_events(self, days=14):
            return []
    reg = es.register_day(date(2026, 9, 14), calendar=_Cal(), path=str(tmp_path / "s.jsonl"),
                          now=ET.localize(datetime(2026, 9, 14, 9, 20)))
    assert reg["event_types"] == ["none"]


def test_registration_is_idempotent(tmp_path):
    path = tmp_path / "s.jsonl"

    class _Cal:
        def get_next_events(self, days=14):
            return []
    now = ET.localize(datetime(2026, 9, 14, 9, 20))
    es.register_day(date(2026, 9, 14), calendar=_Cal(), path=str(path), now=now)
    es.register_day(date(2026, 9, 14), calendar=_Cal(), path=str(path), now=now)
    assert sum(1 for _ in open(path)) == 1


# ── summary ───────────────────────────────────────────────────────

def _row(date_s, types, rule, pnl, status=None):
    return {"kind": "result", "date": date_s, "event_types": types, "rule_id": rule,
            "status": status or es.PRICED, "pnl_net": pnl,
            "rules_version": es.rules_version()}


def test_event_types_are_scored_separately_from_each_other_and_from_controls():
    rows = [_row("2026-09-16", ["FOMC"], "open_follow", +50.0),
            _row("2026-10-13", ["CPI"], "open_follow", -40.0),
            _row("2026-09-14", ["none"], "open_follow", +10.0)]
    s = {(x["event_type"], x["rule_id"]): x for x in es.summarise(rows)}
    assert s[("FOMC", "open_follow")]["wins"] == 1
    assert s[("CPI", "open_follow")]["wins"] == 0
    assert s[("none", "open_follow")]["n"] == 1


def test_unpriced_days_are_counted_but_never_enter_the_win_rate():
    rows = [_row("2026-09-16", ["FOMC"], "open_follow", +50.0),
            _row("2026-10-28", ["FOMC"], "open_follow", None, status=es.UNPRICED)]
    s = {(x["event_type"], x["rule_id"]): x for x in es.summarise(rows)}[("FOMC", "open_follow")]
    assert s["n"] == 1 and s["unpriced"] == 1


def test_the_summary_carries_a_confidence_interval():
    rows = [_row(f"2026-09-{d:02d}", ["none"], "open_follow", +5.0) for d in range(1, 6)]
    s = es.summarise(rows)[0]
    assert s["ci_low"] < s["win_pct"] <= s["ci_high"]


def test_results_from_a_different_rule_version_are_kept_apart():
    """A rule edited after the fact must not merge with pre-registered results."""
    old = dict(_row("2026-09-16", ["FOMC"], "open_follow", +50.0), rules_version="old")
    new = _row("2026-10-28", ["FOMC"], "open_follow", -50.0)
    s = es.summarise([old, new])
    assert sum(x["n"] for x in s) == 1
