"""learning/event_shadow.py -- what would a directional trade have made today?

On event days (FOMC, CPI, NFP, OPEX and their eves) the bot stands aside, and
the learning loop then grades the stand-down against itself: 61 knowledge-base
entries say some version of "the skip was correct", none asks what a trade
would have made, and the daily prediction on those days is literally
"neutral, confidence 0, skip". A week like FOMC-plus-triple-witching teaches
the system nothing.

This module records, for every trading day, what a small set of pre-registered
directional rules WOULD have made, priced after the close from real Polygon
option bars. It places no orders and writes nothing to the trade journal.

Honesty rules, each enforced by tests/test_event_shadow.py:

  * PRE-REGISTERED. Rules live in code and carry a version hash. A registration
    row is written before the open (09:20 ET); a result for a day that was
    never registered is flagged, and results from a different rules version are
    never merged with the current ones.
  * SCORED BY EVENT TYPE. CPI did not replicate FOMC in the June event studies,
    so pooling event types would hide exactly the difference we need to see.
  * WITH A CONTROL. The generic rules also run on ordinary days ("none"). If
    "follow the first 15 minutes" pays just as well on a quiet Tuesday, the
    event is not the edge.
  * NEVER GUESSED. A contract with no bars is UNPRICED; a spread value outside
    its structural bounds is IMPLAUSIBLE. Neither enters a win rate.

Structure (fixed, part of the version hash): a $5-wide debit vertical, long
strike at the nearest $1 to SPY at entry, on the nearest expiry AFTER the event
day that has prices at entry. Not same-day: our own research closed same-day
debit spreads because theta eats them before direction can pay.

Run:
    .venv/bin/python -m learning.event_shadow backfill 2026-09-09 CPI
    .venv/bin/python -m learning.event_shadow summary
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytz
from loguru import logger

import config

ET = pytz.timezone("US/Eastern")
_EASTERN = "US/Eastern"

PRICED = "priced"
UNPRICED = "unpriced"
NO_SIGNAL = "no_signal"
IMPLAUSIBLE = "implausible"
CONTROL = "none"

WIDTH = 5.0                 # $ between long and short strike
HAIRCUT_PER_LEG = 0.05      # last-trade marks are not fills: pay up both ways
SIZE = 1
MAX_EXPIRY_SEARCH = 5       # trading days to look past the event for a priced expiry
CARRY_FORWARD_MINUTES = 5
BOUND_TOLERANCE = 1.02      # two legs' last trades can straddle a few cents


@dataclass(frozen=True)
class Rule:
    rule_id: str
    applies_to: str      # "all", or an event type such as "FOMC"
    signal_start: str    # HH:MM ET — the OPEN of this bar starts the measured move
    signal_end: str      # HH:MM ET — the CLOSE of this bar ends it
    entry: str           # HH:MM ET — priced at this bar's close (strictly after signal)
    exit: str            # HH:MM ET
    mode: str            # "follow" | "fade"
    hypothesis: str


RULES: tuple[Rule, ...] = (
    Rule("open_follow", "all", "09:30", "09:44", "09:50", "15:55", "follow",
         "The first fifteen minutes of trading set the direction for the rest of the day."),
    Rule("open_fade", "all", "09:30", "09:44", "09:50", "15:55", "fade",
         "The first fifteen minutes overshoot, and the rest of the day moves back the other way."),
    Rule("fomc_decision_follow", "FOMC", "13:59", "14:04", "14:06", "15:55", "follow",
         "The market's first five-minute reaction to the Fed decision keeps going into the close."),
    Rule("fomc_presser_fade", "FOMC", "13:59", "14:34", "14:36", "15:55", "fade",
         "The press conference reverses the decision move, so bet against the move since 13:59."),
)


# ── pre-registration ──────────────────────────────────────────────

def rules_version() -> str:
    """Hash of everything that decides a result. Change any of it and new
    results stop merging with old ones."""
    payload = {
        "rules": [dataclasses.asdict(r) for r in RULES],
        "width": WIDTH, "haircut_per_leg": HAIRCUT_PER_LEG, "size": SIZE,
        "max_expiry_search": MAX_EXPIRY_SEARCH,
        "strike_policy": "long at nearest $1 to SPY at entry",
        "expiry_policy": "nearest expiry after the event day with prices at entry",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def applicable_rules(event_types) -> list[Rule]:
    types = set(event_types or [])
    return [r for r in RULES if r.applies_to == "all" or r.applies_to in types]


# ── storage ───────────────────────────────────────────────────────

def _path(path: str | None = None) -> str:
    return path or os.path.join(config.LOG_DIR, "learning", "event_shadow.jsonl")


def load(path: str | None = None) -> list[dict]:
    p = _path(path)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f"event_shadow: skipping malformed line in {p}")
    return rows


def _append(path: str | None, row: dict) -> None:
    p = _path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _now(now=None) -> datetime:
    if now is None:
        return datetime.now(ET)
    return ET.localize(now) if now.tzinfo is None else now.astimezone(ET)


def _calendar_types(day: date, now: datetime, calendar=None) -> list[str]:
    try:
        if calendar is None:
            from data.event_calendar import EventCalendar
            calendar = EventCalendar()
        horizon = max(0, (day - now.date()).days)
        types = set()
        for e in calendar.get_next_events(horizon):
            d = e.get("date")
            d = date.fromisoformat(d) if isinstance(d, str) else d
            if d == day and e.get("type"):
                types.add(str(e["type"]))
        return sorted(types) or [CONTROL]
    except Exception as e:
        logger.warning(f"event_shadow: calendar unavailable ({e}); filing {day} as control")
        return [CONTROL]


def register_day(day: date | None = None, *, calendar=None, path: str | None = None,
                 now=None) -> dict:
    """Write today's pre-registration row before the open. Idempotent."""
    now = _now(now)
    day = day or now.date()
    rv = rules_version()
    for r in load(path):
        if (r.get("kind") == "registration" and r.get("date") == day.isoformat()
                and r.get("rules_version") == rv):
            return r
    types = _calendar_types(day, now, calendar)
    row = {
        "kind": "registration",
        "date": day.isoformat(),
        "event_types": types,
        "rules_version": rv,
        "rules": [dataclasses.asdict(r) for r in applicable_rules(types)],
        "registered_at": now.isoformat(),
    }
    _append(path, row)
    return row


# ── pricing one rule on one day ───────────────────────────────────

def _ts(day: date, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day.isoformat()} {hhmm}").tz_localize(_EASTERN)


def _session(spy: pd.DataFrame | None) -> pd.DataFrame | None:
    if spy is None or spy.empty:
        return spy
    spy = spy[~spy.index.duplicated(keep="last")]
    t = spy.index.strftime("%H:%M")
    return spy[(t >= "09:30") & (t <= "16:00")]


def _spy_close_at(spy, ts) -> float | None:
    if spy is None or spy.empty:
        return None
    s = spy[spy.index <= ts]
    if s.empty or ts - s.index[-1] > pd.Timedelta(minutes=CARRY_FORWARD_MINUTES):
        return None
    return float(s["close"].iloc[-1])


def _spy_open_at(spy, ts) -> float | None:
    if spy is not None and not spy.empty and ts in spy.index:
        return float(spy.loc[ts, "open"])
    return _spy_close_at(spy, ts - pd.Timedelta(minutes=1))


def _next_trading_day(d: date) -> date:
    d = d + timedelta(days=1)
    while not config.is_trading_day(d):
        d += timedelta(days=1)
    return d


def _legs(direction: str, strike: float, expiry: date) -> list[dict]:
    e = expiry.isoformat()
    if direction == "bullish":
        return [{"action": "BUY", "option_type": "call", "strike": strike, "expiration": e},
                {"action": "SELL", "option_type": "call", "strike": strike + WIDTH, "expiration": e}]
    return [{"action": "BUY", "option_type": "put", "strike": strike, "expiration": e},
            {"action": "SELL", "option_type": "put", "strike": strike - WIDTH, "expiration": e}]


def _simulate(rule: Rule, day: date, spy, history, use_cache: bool) -> dict:
    from data.options_history import value_at
    from journal.trade_recorder import round_trip_commission

    start = _spy_open_at(spy, _ts(day, rule.signal_start))
    end = _spy_close_at(spy, _ts(day, rule.signal_end))
    if start is None or end is None:
        return {"status": UNPRICED, "reason": "no SPY bar at the signal time"}
    move = round(end - start, 4)
    out = {"signal_move": move, "spy_signal_start": start, "spy_signal_end": end}
    if move == 0:
        return {**out, "status": NO_SIGNAL}

    up = move > 0
    direction = "bullish" if (up if rule.mode == "follow" else not up) else "bearish"
    entry_ts, exit_ts = _ts(day, rule.entry), _ts(day, rule.exit)
    spy_entry = _spy_close_at(spy, entry_ts)
    out.update(direction=direction, spy_entry=spy_entry,
               spy_exit=_spy_close_at(spy, exit_ts),
               entry_ts=entry_ts.isoformat(), exit_ts=exit_ts.isoformat())
    if spy_entry is None:
        return {**out, "status": UNPRICED, "reason": "no SPY bar at entry"}

    strike = float(round(spy_entry))
    expiry = _next_trading_day(day)
    first_legs, series, entry_value = None, None, None
    for _ in range(MAX_EXPIRY_SEARCH):
        legs = _legs(direction, strike, expiry)
        first_legs = first_legs or legs
        series = history.structure_minutes("SPY", day, legs, use_cache=use_cache)
        entry_value = value_at(series, entry_ts)
        if entry_value is not None:
            break
        expiry = _next_trading_day(expiry)
    if entry_value is None:
        return {**out, "status": UNPRICED, "legs": first_legs,
                "reason": "no priced expiry at entry"}

    exit_value = value_at(series, exit_ts)
    out.update(legs=legs, expiry=expiry.isoformat(), entry_value=round(entry_value, 4))
    if exit_value is None:
        return {**out, "status": UNPRICED, "reason": "no price at exit"}
    out["exit_value"] = round(exit_value, 4)

    bound = WIDTH * BOUND_TOLERANCE
    if not (0 < entry_value <= bound) or not (-(bound - WIDTH) <= exit_value <= bound):
        return {**out, "status": IMPLAUSIBLE,
                "reason": f"a ${WIDTH:g}-wide debit spread must be worth between $0 and ${WIDTH:g}"}

    haircut = HAIRCUT_PER_LEG * len(legs)
    paid = entry_value + haircut
    received = max(exit_value - haircut, 0.0)
    gross = round((received - paid) * 100 * SIZE, 2)
    commission = round_trip_commission("debit_spread", legs, SIZE)
    return {**out, "status": PRICED, "pnl_gross": gross, "commission": commission,
            "pnl_net": round(gross - commission, 2)}


def resolve_day(day: date, *, event_types=None, history=None, calendar=None,
                path: str | None = None, now=None) -> list[dict]:
    """Price every applicable rule for `day`. Returns the NEW result rows.

    Idempotent per (date, rule, rules_version). Same-day runs bypass the option
    cache: it also stores empty results, so a fetch that ran before the data
    settled would freeze "no bars" permanently.
    """
    now = _now(now)
    day_s, rv = day.isoformat(), rules_version()
    rows = load(path)
    reg = next((r for r in rows if r.get("kind") == "registration"
                and r.get("date") == day_s and r.get("rules_version") == rv), None)
    registered = reg is not None
    if event_types is None:
        event_types = reg["event_types"] if reg else _calendar_types(day, now, calendar)
    done = {r.get("rule_id") for r in rows if r.get("kind") == "result"
            and r.get("date") == day_s and r.get("rules_version") == rv}
    todo = [r for r in applicable_rules(event_types) if r.rule_id not in done]
    if not todo:
        return []

    from data.options_history import OptionsHistory, to_eastern
    history = history or OptionsHistory()
    use_cache = day < now.date()
    spy = _session(to_eastern(history.get_aggs("SPY", 1, "minute", day, day,
                                               use_cache=use_cache)))
    new = []
    for rule in todo:
        try:
            res = _simulate(rule, day, spy, history, use_cache)
        except Exception as e:  # one broken rule must not lose the others
            logger.exception(f"event_shadow: {rule.rule_id} on {day_s} failed: {e}")
            res = {"status": UNPRICED, "reason": f"error: {e}"}
        row = {"kind": "result", "date": day_s, "event_types": list(event_types),
               "rule_id": rule.rule_id, "rules_version": rv, "registered": registered,
               "resolved_at": now.isoformat(), **res}
        _append(path, row)
        new.append(row)
    return new


# ── summary ───────────────────────────────────────────────────────

def summarise(rows: list[dict] | None = None, *, path: str | None = None,
              version: str | None = None) -> list[dict]:
    """Per (event type, rule): priced n, wins, win%, 95% CI, net P&L.
    Unpriced, no-signal and implausible days are counted, never scored."""
    from learning.forward_scorecard import wilson
    rows = load(path) if rows is None else rows
    version = version or rules_version()
    agg: dict[tuple, dict] = {}
    for r in rows:
        if r.get("kind") != "result" or r.get("rules_version") != version:
            continue
        for et in (r.get("event_types") or [CONTROL]):
            a = agg.setdefault((et, r.get("rule_id")), {
                "event_type": et, "rule_id": r.get("rule_id"), "n": 0, "wins": 0,
                "total_net": 0.0, "unpriced": 0, "no_signal": 0, "implausible": 0})
            st = r.get("status")
            if st == PRICED and r.get("pnl_net") is not None:
                a["n"] += 1
                a["wins"] += 1 if r["pnl_net"] > 0 else 0
                a["total_net"] += float(r["pnl_net"])
            elif st == NO_SIGNAL:
                a["no_signal"] += 1
            elif st == IMPLAUSIBLE:
                a["implausible"] += 1
            else:
                a["unpriced"] += 1
    out = []
    for a in agg.values():
        n = a["n"]
        a["win_pct"] = round(a["wins"] / n * 100, 1) if n else 0.0
        a["avg_net"] = round(a["total_net"] / n, 2) if n else 0.0
        a["total_net"] = round(a["total_net"], 2)
        lo, hi = wilson(a["wins"], n) if n else (0.0, 0.0)
        a["ci_low"], a["ci_high"] = round(lo, 1), round(hi, 1)
        out.append(a)
    return sorted(out, key=lambda a: (a["event_type"] == CONTROL, a["event_type"], a["rule_id"]))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "backfill" and len(argv) >= 2:
        day = date.fromisoformat(argv[1])
        types = argv[2:] or [CONTROL]
        for r in resolve_day(day, event_types=types):
            print(f"{r['date']} {','.join(r['event_types']):12} {r['rule_id']:22} "
                  f"{r['status']:11} {r.get('direction', ''):8} net={r.get('pnl_net')}")
        return
    for a in summarise():
        print(f"{a['event_type']:9} {a['rule_id']:22} n={a['n']:3} win={a['win_pct']:5.1f}% "
              f"CI[{a['ci_low']:.0f}-{a['ci_high']:.0f}] avg=${a['avg_net']:+.2f} "
              f"unpriced={a['unpriced']} no_signal={a['no_signal']}")


if __name__ == "__main__":
    main()
