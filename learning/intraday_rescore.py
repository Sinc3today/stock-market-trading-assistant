"""learning/intraday_rescore.py -- re-measure the intraday sandbox against real prices.

52 closed intraday records exist and not one of them measured what it claims:

  * Entry prices came from last trades struck at different moments — a median
    32% from the market, worst 175% (3A87F4C4: $2.01 recorded, $0.73 real).
    Fixed for new trades on 2026-09-15; these records keep the old numbers.
  * Every same-day trade was closed five minutes after entry, because the time
    stop was `dte <= 0`, true from the first check. Fixed 2026-09-13.
  * 18 were "stopped at 75% of max loss" while the real spread sat anywhere
    from 35% down to 40% UP.

So the sandbox's win rate, its P&L, and the nightly reflection's conclusions
describe five-minute holds at prices that never traded.

This replays each trade against real per-contract minute bars under the rules
the strategy was SUPPOSED to follow — exit_rule_for()'s target and stop,
checked every five minutes like the live cron, plus the restored forced close.

DRY RUN. It writes nothing to the journal. A replay is a reconstruction: it is
measured, not remembered, and whether any of it is written back is a separate
decision for a human.

Run:
    .venv/bin/python -m learning.intraday_rescore
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from loguru import logger

import config
from journal.trade_recorder import _pnl_convention, round_trip_commission
from learning import forward_scorecard as fs
from learning.exit_manager import exit_rule_for

ET = pytz.timezone("US/Eastern")

# The live intraday exit cron is minute="*/5" — the replay must not be luckier
# than the system it is measuring, so it looks at the same clock marks.
CADENCE_MINUTES = 5
INTRADAY_BUCKETS = ("0DTE", "1-3DTE")
SESSION_OPEN = (9, 30)
SESSION_CLOSE_HOUR = 16

REPLAYED, UNPRICED, SKIPPED = "replayed", "unpriced", "skipped"
ARTIFACT = "intraday_rescore_dryrun.json"


# ── geometry ──────────────────────────────────────────────────────

def structure_width(legs) -> float | None:
    """Widest vertical inside the structure, in points.

    Puts and calls are measured separately: a condor can only be breached on
    one side, so its risk is the wider wing.
    """
    widest = 0.0
    for cp in ("call", "put"):
        strikes = [float(l["strike"]) for l in (legs or [])
                   if str(l.get("option_type") or l.get("type")).lower() == cp
                   and l.get("strike") is not None]
        if len(strikes) >= 2:
            widest = max(widest, max(strikes) - min(strikes))
    return widest or None


def risk_from_entry(strategy, legs, entry) -> tuple[float, float]:
    """(max_profit, max_loss) in dollars per contract, from the REAL entry.

    The recorded max_profit/max_loss were derived from the recorded entry, so
    replaying against them would re-import the very error being measured.
    """
    width = structure_width(legs)
    if width is None or entry is None:
        return (0.0, 0.0)
    entry = abs(float(entry))
    if _pnl_convention(strategy) == "credit":
        return (round(entry * 100, 2), round((width - entry) * 100, 2))
    return (round((width - entry) * 100, 2), round(entry * 100, 2))


# ── the walk ──────────────────────────────────────────────────────

def _entry_ts(trade):
    try:
        return ET.localize(datetime.strptime(str(trade.get("entry_date"))[:19],
                                             "%Y-%m-%d %I:%M %p"))
    except (TypeError, ValueError):
        return None


def _expiry(legs):
    days = [str(l.get("expiration") or l.get("expiry"))[:10] for l in (legs or [])
            if (l.get("expiration") or l.get("expiry"))]
    try:
        return date.fromisoformat(min(days)) if days else None
    except ValueError:
        return None


def _at(day: date, hour: int, minute: int):
    return ET.localize(datetime(day.year, day.month, day.day, hour, minute))


def _check_marks(start, end):
    """Clock-aligned five-minute marks strictly after `start`, through `end`."""
    t = (start + timedelta(minutes=1)).replace(second=0, microsecond=0)
    t += timedelta(minutes=(-t.minute) % CADENCE_MINUTES)
    while t <= end:
        yield t
        t += timedelta(minutes=CADENCE_MINUTES)


def replay_trade(trade: dict, history=None, rules=None) -> dict:
    """Replay one recorded trade against real bars. Never writes."""
    legs = trade.get("legs") or []
    bucket, strategy = trade.get("dte_bucket"), trade.get("strategy")
    size = int(trade.get("size") or 1)
    row = {"trade_id": trade.get("trade_id"), "book": fs.book_of(trade),
           "dte_bucket": bucket, "strategy": strategy, "size": size,
           "status": SKIPPED, "entry_error_pct": None,
           "recorded": {"entry": trade.get("entry_price"),
                        "exit": trade.get("exit_price"),
                        "outcome": trade.get("outcome"),
                        "pnl_net": fs.net_pnl(trade)},
           "replayed": {}}
    if bucket not in INTRADAY_BUCKETS or not legs:
        return row
    ts0, expiry = _entry_ts(trade), _expiry(legs)
    if ts0 is None or expiry is None:
        return row

    if history is None:
        from data.options_history import OptionsHistory
        history = OptionsHistory()
    from data.options_history import value_at

    cache: dict = {}

    def series_for(day):
        if day not in cache:
            cache[day] = history.structure_minutes("SPY", day, legs)
        return cache[day]

    signed_entry = value_at(series_for(ts0.date()), ts0)
    if signed_entry is None or abs(float(signed_entry)) < 0.01:
        row["status"] = UNPRICED
        row["replayed"] = {"entry": None, "pnl_net": None,
                           "reason": "no real price at the recorded entry minute"}
        return row

    entry = abs(float(signed_entry))
    rules = rules or exit_rule_for(strategy, bucket)
    credit = _pnl_convention(strategy) == "credit"
    max_profit, max_loss = risk_from_entry(strategy, legs, entry)
    max_profit, max_loss = max_profit * size, max_loss * size
    target_pct, stop_pct = rules.get("profit_target_pct"), rules.get("stop_pct")
    forced_time = rules.get("forced_close_time")
    before_expiry = rules.get("forced_close_minutes_before_expiry")

    exit_signed = exit_ts = reason = None
    day = ts0.date()
    while day <= expiry and exit_ts is None:
        series = series_for(day)
        start = ts0 if day == ts0.date() else _at(day, *SESSION_OPEN) - timedelta(minutes=1)
        for ts in _check_marks(start, _at(day, SESSION_CLOSE_HOUR, 0)):
            v = value_at(series, ts)
            if v is None:
                continue                      # no print: skip, never fabricate
            v = float(v)
            pnl = ((v - entry) if not credit else (entry + v)) * 100 * size
            if target_pct and max_profit > 0 and pnl / max_profit >= target_pct:
                exit_signed, exit_ts, reason = v, ts, f"profit target {target_pct:.0%}"
                break
            if stop_pct is not None and max_loss > 0 and pnl <= -stop_pct * max_loss:
                exit_signed, exit_ts, reason = v, ts, f"stop {stop_pct:.0%} of max loss"
                break
            minute_of_day = ts.hour * 60 + ts.minute
            if forced_time:
                h, m = (int(x) for x in forced_time.split(":"))
                if minute_of_day >= h * 60 + m:
                    exit_signed, exit_ts, reason = v, ts, f"forced close {forced_time} ET"
                    break
            if (before_expiry is not None and day == expiry
                    and minute_of_day >= SESSION_CLOSE_HOUR * 60 - int(before_expiry)):
                exit_signed, exit_ts, reason = v, ts, (
                    f"forced close {int(before_expiry)}m before expiry")
                break
        day += timedelta(days=1)
        while day <= expiry and not config.is_trading_day(day):
            day += timedelta(days=1)

    recorded_entry = trade.get("entry_price")
    if recorded_entry not in (None, ""):
        row["entry_error_pct"] = round((abs(float(recorded_entry)) - entry) / entry * 100, 1)

    if exit_ts is None:
        row["status"] = UNPRICED
        row["replayed"] = {"entry": round(entry, 3), "pnl_net": None,
                           "reason": "no priced minute produced an exit"}
        return row

    exit_value = abs(float(exit_signed))
    haircut = config.REPLAY_HAIRCUT_PER_LEG * len(legs)
    if credit:
        gross = ((entry - haircut) - (exit_value + haircut)) * 100 * size
    else:
        gross = ((exit_value - haircut) - (entry + haircut)) * 100 * size
    fee = round_trip_commission(strategy, legs, size)
    net = round(gross - fee, 2)
    row["status"] = REPLAYED
    row["replayed"] = {
        "entry": round(entry, 3), "exit": round(exit_value, 3),
        "exit_ts": exit_ts.isoformat(), "reason": reason,
        "held_minutes": int((exit_ts - ts0).total_seconds() / 60),
        "max_profit": max_profit, "max_loss": max_loss,
        "pnl_gross": round(gross, 2), "commission": fee, "pnl_net": net,
        "outcome": "win" if net > 0 else "loss" if net < 0 else "breakeven",
    }
    return row


# ── the comparison ────────────────────────────────────────────────

def plan(trades: list[dict] | None = None, history=None) -> list[dict]:
    """Replay every intraday record. Returns rows; writes nothing."""
    if trades is None:
        from journal.trade_recorder import TradeRecorder
        trades = TradeRecorder().get_all_trades()
    rows = []
    for t in trades:
        if t.get("dte_bucket") not in INTRADAY_BUCKETS:
            continue
        try:
            rows.append(replay_trade(t, history=history))
        except Exception as e:      # one bad record must not lose the rest
            logger.exception(f"intraday_rescore: {t.get('trade_id')} failed: {e}")
    return rows


def summarise(rows: list[dict]) -> list[dict]:
    """Recorded vs replayed, per (book, bucket), with both intervals."""
    agg: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("book"), r.get("dte_bucket"))
        a = agg.setdefault(key, {"book": key[0], "dte_bucket": key[1],
                                 "n": 0, "replayed_wins": 0, "replayed_net": 0.0,
                                 "recorded_n": 0, "recorded_wins": 0,
                                 "recorded_net": 0.0, "unpriced": 0,
                                 "entry_errors": []})
        if r["status"] != REPLAYED:
            a["unpriced"] += 1
            continue
        a["n"] += 1
        net = r["replayed"]["pnl_net"]
        a["replayed_wins"] += 1 if net > 0 else 0
        a["replayed_net"] += net
        rec = r["recorded"].get("pnl_net")
        if rec is not None:
            a["recorded_n"] += 1
            a["recorded_wins"] += 1 if rec > 0 else 0
            a["recorded_net"] += rec
        if r.get("entry_error_pct") is not None:
            a["entry_errors"].append(abs(r["entry_error_pct"]))
    out = []
    for a in agg.values():
        n, rn = a["n"], a["recorded_n"]
        a["replayed_win_pct"] = round(a["replayed_wins"] / n * 100, 1) if n else 0.0
        a["recorded_win_pct"] = round(a["recorded_wins"] / rn * 100, 1) if rn else 0.0
        a["replayed_ci_low"], a["replayed_ci_high"] = fs.wilson(a["replayed_wins"], n)
        a["recorded_ci_low"], a["recorded_ci_high"] = fs.wilson(a["recorded_wins"], rn)
        a["replayed_net"] = round(a["replayed_net"], 2)
        a["recorded_net"] = round(a["recorded_net"], 2)
        errs = sorted(a.pop("entry_errors"))
        a["median_entry_error_pct"] = errs[len(errs) // 2] if errs else None
        out.append(a)
    return sorted(out, key=lambda a: (str(a["book"]), str(a["dte_bucket"])))


def main():
    rows = plan()
    print(f"{'id':9} {'book':11} {'bucket':7} {'strategy':18} {'status':9} "
          f"{'rec':>6} {'real':>6} {'err':>7} {'held':>7} {'rec net':>9} "
          f"{'new net':>9}  reason")

    def money(v, w=9):
        return ("-" if v is None else f"{v:,.2f}").rjust(w)

    for r in sorted(rows, key=lambda r: (str(r["book"]), str(r["dte_bucket"]),
                                         str(r["trade_id"]))):
        p, rec = r["replayed"], r["recorded"]
        err = "-" if r["entry_error_pct"] is None else f"{r['entry_error_pct']:+.0f}%"
        held = "-" if p.get("held_minutes") is None else f"{p['held_minutes']}m"
        print(f"{str(r['trade_id']):9} {str(r['book']):11} {str(r['dte_bucket']):7} "
              f"{str(r['strategy']):18} {r['status']:9} {money(rec.get('entry'), 6)} "
              f"{money(p.get('entry'), 6)} {err:>7} {held:>7} "
              f"{money(rec.get('pnl_net'))} {money(p.get('pnl_net'))}  "
              f"{p.get('reason', '')}")
    print()
    for s_ in summarise(rows):
        print(f"{str(s_['book']):11} {str(s_['dte_bucket']):7} n={s_['n']:3} "
              f"recorded {s_['recorded_win_pct']:5.1f}% "
              f"[{s_['recorded_ci_low']:.0f}-{s_['recorded_ci_high']:.0f}] "
              f"${s_['recorded_net']:+9,.2f}   "
              f"replayed {s_['replayed_win_pct']:5.1f}% "
              f"[{s_['replayed_ci_low']:.0f}-{s_['replayed_ci_high']:.0f}] "
              f"${s_['replayed_net']:+9,.2f}   "
              f"median entry error {s_['median_entry_error_pct']}%  "
              f"unpriced {s_['unpriced']}")
    path = os.path.join(config.LOG_DIR, "learning", ARTIFACT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"generated_at": datetime.now(ET).isoformat(),
                   "rows": rows, "summary": summarise(rows)}, fh, indent=2, default=str)
    print(f"\ndry run only — nothing written to the journal. Detail: {path}")


if __name__ == "__main__":
    main()
