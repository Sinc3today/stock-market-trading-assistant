"""
backtests/intraday_router_wf.py -- Walk-forward backtest of the Phase 3
intraday entry router.

Wraps backtests/intraday_backtest.simulate_0dte_day with
signals/intraday_entry_router.route. Runs treatment (router-gated) vs
baseline (tier-gate disabled) on identical days, identical structures.
Emits raw per-window stats; verdict thresholds are TBD via a follow-up
calibration exercise.

Spec: docs/superpowers/specs/2026-05-28-intraday-router-wf-design.md
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─────────────────────────────────────────────────────────────
# Mock broker — satisfies signals.intraday_entry_router.route's
# dedup-state queries with per-day in-memory state.
# ─────────────────────────────────────────────────────────────

class _MockBroker:
    """Minimal broker stub for route(). Fresh-per-day in the runner so
    cross-day state can't leak. Implements only the two methods route()
    calls: trades.get_trades_by and _entry_count_today_by_combo."""

    def __init__(self):
        self.trades = self   # adapter so route() can call .trades.get_trades_by()
        self._opens: list[dict] = []

    def get_trades_by(self, *, strategy: str, dte_bucket: str) -> list[dict]:
        return [t for t in self._opens
                if t["strategy"] == strategy and t["dte_bucket"] == dte_bucket]

    def _entry_count_today_by_combo(self, strategy: str, dte_bucket: str) -> int:
        return len(self.get_trades_by(strategy=strategy, dte_bucket=dte_bucket))

    def record_open(self, *, strategy: str, dte_bucket: str) -> None:
        self._opens.append({
            "strategy":   strategy,
            "dte_bucket": dte_bucket,
            "outcome":    "open",
        })


from contextlib import contextmanager

import config


@contextmanager
def _bypass_tier_gate():
    """Temporarily set config.ENTRY_TIER_MINIMUM = 'watch' (the lowest rank
    in signals.intraday_entry_router._TIER_RANK) so route()'s tier gate
    admits everything. Used to compute the BASELINE side of the WF
    comparison — DTE assignment and dedup remain identical to treatment,
    so the only delta is the tier filter.

    Restoration is guaranteed: the original value is captured at __enter__,
    not read from config at __exit__, so caller mutations inside the
    block don't break restoration.
    """
    original = config.ENTRY_TIER_MINIMUM
    config.ENTRY_TIER_MINIMUM = "watch"
    try:
        yield
    finally:
        config.ENTRY_TIER_MINIMUM = original


from datetime import date, timedelta
from typing import Iterator


def _add_months(d: date, n: int) -> date:
    """Add n calendar months to date d, clipping the day to the new month's
    last day if necessary. Used for window boundary math."""
    month = d.month + n
    year  = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    # Clip day to month's last day to avoid 31->Feb errors.
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def generate_windows(start: date, end: date,
                     train_months: int = 6, test_months: int = 3,
                     step_months: int = 1
                     ) -> Iterator[tuple[tuple[date, date], tuple[date, date]]]:
    """Yield (train_range, test_range) tuples where each range is
    (start_date_inclusive, end_date_inclusive).

    Sliding walk-forward: train covers `train_months` calendar months
    immediately preceding test; test covers the next `test_months`. Each
    iteration advances the test_start by `step_months`. Stops when the test
    range would overshoot `end`.

    Train window has no learning role in this spec — it's a contextual
    placeholder for a future learning step.
    """
    # Anchor test_start to first-of-month so windows align to calendar
    # months regardless of the input `start` day-of-month.
    anchored = _add_months(start, train_months)
    test_start = date(anchored.year, anchored.month, 1)
    while True:
        train_start = _add_months(test_start, -train_months)
        train_end   = test_start - timedelta(days=1)
        test_end    = _add_months(test_start, test_months) - timedelta(days=1)
        if test_end > end:
            return
        yield ((train_start, train_end), (test_start, test_end))
        test_start = _add_months(test_start, step_months)


STRATEGY_NOT_SUPPORTED = object()   # sentinel — router emitted a strategy
                                     # backtests/intraday_backtest.py can't price


def _strategy_to_structure(strategy: str, direction: str):
    """Map signals.intraday_entry_router setup.strategy → backtests.
    intraday_backtest structure name. Returns STRATEGY_NOT_SUPPORTED if
    the strategy can't be priced (out of scope for v1)."""
    if strategy == "iron_condor":
        return "iron_condor"
    if strategy == "call_debit_spread":
        return "bull_debit"
    if strategy == "put_debit_spread":
        return "bear_debit"
    return STRATEGY_NOT_SUPPORTED


def simulate_short_dte_day(day, structure: str, dte_bucket: str,
                            spy_intraday, options_history):
    """Wrap backtests.intraday_backtest.simulate_0dte_day to support 0DTE
    AND 1-3DTE in the same call. The 0DTE path delegates directly; the
    1-3DTE path picks a future-expiration contract and exits at session
    close instead of the 0DTE EOD pin/assignment flatten.

    Treatment and baseline both call this with require_confirmation=False
    so the router IS the entry filter (OR+VWAP would double-gate otherwise).

    Returns simulate_0dte_day's result dict, or None when the day can't
    be priced.
    """
    from datetime import timedelta
    from backtests.intraday_backtest import simulate_0dte_day

    if dte_bucket == "0DTE":
        return simulate_0dte_day(
            day, structure, spy_intraday, options_history,
            require_confirmation=False,   # router replaces OR+VWAP
        )

    if dte_bucket == "1-3DTE":
        return _simulate_short_dte_with_expiration(
            day, day + timedelta(days=2),
            structure, spy_intraday, options_history,
        )

    return None   # unknown bucket — caller's bug


def _simulate_short_dte_with_expiration(day, expiry,
                                         structure: str,
                                         spy_intraday, options_history):
    """1-3DTE same-session simulator. Same opening-range entry as the 0DTE
    simulator, but the option contract has `expiry > day`, so:
      - There's no pin/assignment risk on `day`, hence no 15:45 flatten —
        we exit at the regular session close (16:00) or on target/stop.
      - This is a SAME-DAY-MARK approximation: we record entry-to-close
        PnL on `day` for a contract that has additional days to live.
        Full multi-day PnL is out of scope for v1 — documented in spec.
    """
    from datetime import datetime, timedelta, time
    from data.options_history import option_ticker
    from backtests.intraday_backtest import (
        _to_et, _spread_value, build_0dte_legs, is_credit_structure,
        MARKET_OPEN_ET, OR_MINUTES, COMMISSION_PER_LEG, SLIPPAGE,
        PROFIT_TARGET_PCT, STOP_MULT,
    )
    import pandas as pd

    if spy_intraday is None or spy_intraday.empty:
        return None
    spy = _to_et(spy_intraday)
    SESSION_CLOSE_ET = time(16, 0)
    rth = spy[(spy.index.time >= MARKET_OPEN_ET) & (spy.index.time <= SESSION_CLOSE_ET)]
    if rth.empty:
        return None

    or_end = (datetime.combine(day, MARKET_OPEN_ET) + timedelta(minutes=OR_MINUTES)).time()
    session = rth[rth.index.time >= or_end]
    if session.empty:
        return None

    entry_ts   = session.index[0]
    entry_spot = float(session.iloc[0]["close"])

    legs = build_0dte_legs(entry_spot, structure)
    if not legs:
        return None

    leg_closes = []
    for leg in legs:
        contract = option_ticker("SPY", expiry, leg["cp"], leg["strike"])
        df = options_history.get_aggs(contract, 5, "minute", day, day)
        if df.empty:
            return None
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
    # build_structure(entry_ts=entry_ts, expiry=expiry) matches this function's
    # semantics: uses the explicit future expiry for option_ticker lookups and
    # marks at entry_ts (opening-range end, ~9:45 ET).
    from signals.intraday_structure_builder import build_structure, HistoricalPricer
    _built = build_structure(structure, "1-3DTE", entry_spot,
                             HistoricalPricer(options_history),
                             as_of=day, entry_ts=entry_ts, expiry=expiry)
    if _built is None:
        return None
    # Apply slippage and compute max_profit using the same formula as before.
    # The builder's max_profit/max_loss are pre-slippage and intentionally NOT used
    # here — the backtest recomputes them on the post-slippage entry to preserve
    # walk-forward parity. Do not "simplify" to _built["max_profit"].
    # (The builder returns the raw spread value; slippage is a backtest concern.)
    credit = is_credit_structure(structure)
    entry_px = _built["entry_price"]
    entry_px = (entry_px - SLIPPAGE) if credit else (entry_px + SLIPPAGE)
    if entry_px <= 0:
        return None

    width      = abs(legs[0]["strike"] - legs[1]["strike"]) if len(legs) >= 2 else 0
    max_profit = entry_px * 100 if credit else (width - entry_px) * 100
    commission = COMMISSION_PER_LEG * len(legs) * 2

    exit_reason = "session_close"
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
        if STOP_MULT is not None and pnl <= -STOP_MULT * max_profit:
            exit_reason = "stop"; break

    return {
        "date": day.isoformat(), "structure": structure,
        "entry_spot": round(entry_spot, 2), "entry_px": round(entry_px, 2),
        "pnl_dollars": round(pnl, 2),
        "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "exit_reason": exit_reason,
    }


import math
import statistics
from collections import Counter


def _sharpe(pnls: list[float]) -> float:
    """Per-trade Sharpe: mean / stdev. Returns 0.0 when n<2 (undefined stdev)."""
    if len(pnls) < 2:
        return 0.0
    sd = statistics.stdev(pnls)
    if sd == 0:
        return 0.0
    return statistics.mean(pnls) / sd


def window_stats(trades_T: list[dict], trades_B: list[dict]) -> dict:
    """Aggregate per-window stats. Trades are dicts from simulate_short_dte_day
    with at least 'pnl_dollars', 'strategy', 'dte_bucket'. Either side may
    be empty (e.g. baseline returns no trades for a window — unlikely but
    possible if all setups failed the engine's score floor)."""

    def _aggregate(trades):
        n = len(trades)
        pnls = [t["pnl_dollars"] for t in trades]
        return {
            "n":      n,
            "pnl":    sum(pnls) if pnls else 0.0,
            "mean":   (sum(pnls) / n) if n else 0.0,
            "sharpe": _sharpe(pnls),
            "wins":   sum(1 for p in pnls if p > 0),
        }

    T = _aggregate(trades_T)
    B = _aggregate(trades_B)

    # Per-bucket breakdown (0DTE / 1-3DTE).
    buckets = sorted({t["dte_bucket"] for t in trades_T} |
                     {t["dte_bucket"] for t in trades_B})
    by_bucket = {}
    for b in buckets:
        bT = _aggregate([t for t in trades_T if t["dte_bucket"] == b])
        bB = _aggregate([t for t in trades_B if t["dte_bucket"] == b])
        by_bucket[b] = {
            "n_trades_T": bT["n"], "n_trades_B": bB["n"],
            "pnl_T":      bT["pnl"], "pnl_B":      bB["pnl"],
            "sharpe_T":   bT["sharpe"], "sharpe_B": bB["sharpe"],
        }

    return {
        "n_trades_T":           T["n"],
        "n_trades_B":           B["n"],
        "pnl_T":                T["pnl"],
        "pnl_B":                B["pnl"],
        "sharpe_T":             T["sharpe"],
        "sharpe_B":             B["sharpe"],
        "win_rate_T":           (T["wins"] / T["n"]) if T["n"] else 0.0,
        "win_rate_B":           (B["wins"] / B["n"]) if B["n"] else 0.0,
        "delta_pnl_per_trade":  (T["mean"] - B["mean"]) if (T["n"] and B["n"]) else float("nan"),
        "delta_sharpe":         T["sharpe"] - B["sharpe"],
        "by_bucket":            by_bucket,
    }


# ─────────────────────────────────────────────────────────────
# Verdict thresholds — TBD via separate calibration exercise.
# When ALL of these are None, window_verdict returns 'raw'.
# ─────────────────────────────────────────────────────────────
MIN_DELTA_PNL_PER_TRADE: float | None = None
MIN_OOS_PNL:             float | None = None
MIN_OOS_SHARPE:          float | None = None
MIN_OOS_WIN_RATE:        float | None = None


def window_verdict(stats: dict, min_n: int = 10) -> str:
    """Returns one of 'raw', 'inconclusive', 'pass', 'fail'.

      'raw'          — thresholds not yet calibrated; stats emitted only
      'inconclusive' — n_trades_T < min_n
      'pass'         — all thresholds met
      'fail'         — at least one threshold missed
    """
    thresholds = (MIN_DELTA_PNL_PER_TRADE, MIN_OOS_PNL,
                  MIN_OOS_SHARPE, MIN_OOS_WIN_RATE)
    if stats.get("n_trades_T", 0) < min_n:
        return "inconclusive"
    if all(t is None for t in thresholds):
        return "raw"
    if (MIN_DELTA_PNL_PER_TRADE is not None
            and stats["delta_pnl_per_trade"] < MIN_DELTA_PNL_PER_TRADE):
        return "fail"
    if MIN_OOS_PNL is not None and stats["pnl_T"] < MIN_OOS_PNL:
        return "fail"
    if MIN_OOS_SHARPE is not None and stats["sharpe_T"] < MIN_OOS_SHARPE:
        return "fail"
    if MIN_OOS_WIN_RATE is not None and stats["win_rate_T"] < MIN_OOS_WIN_RATE:
        return "fail"
    return "pass"


def aggregate_verdict(window_results: list[dict]) -> dict:
    """Aggregate per-window verdicts into headline pass-rate. 'inconclusive'
    is excluded from the pass-rate denominator. 'raw' windows mean
    thresholds aren't set yet — pass_rate is None in that case."""
    counts = Counter(r["verdict"] for r in window_results)
    n_pass         = counts.get("pass", 0)
    n_fail         = counts.get("fail", 0)
    n_inconclusive = counts.get("inconclusive", 0)
    n_raw          = counts.get("raw", 0)
    determinative  = n_pass + n_fail
    return {
        "n_windows":      len(window_results),
        "n_pass":         n_pass,
        "n_fail":         n_fail,
        "n_inconclusive": n_inconclusive,
        "n_raw":          n_raw,
        "pass_rate":      (n_pass / determinative) if determinative else None,
    }


import pytz
from datetime import datetime
from loguru import logger
from signals.intraday_entry_router import route as _route_entry


_ET = pytz.timezone("US/Eastern")


def _iter_trading_days(start: date, end: date) -> Iterator[date]:
    """Yield weekdays in [start, end] inclusive. Holiday handling deferred —
    setup builder returning [] for a holiday day is the natural skip path."""
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d = d + timedelta(days=1)


def run_window(*, train_range, test_range, get_setup, get_pnl) -> dict:
    """Run one walk-forward window and return its results.

    Parameters
    ----------
    train_range : (date, date) — contextual, no learning role this spec
    test_range  : (date, date) — OOS evaluation period
    get_setup   : Callable[[date], list[SPYSetup]] — usually
                  router_setup_builder.build_historical_setup
    get_pnl     : Callable[[date, setup, strategy, dte_bucket], dict|None]
                  — usually a closure around simulate_short_dte_day +
                  OptionsHistory + cached spy_intraday

    Dependency injection lets the unit tests substitute pure-function stubs.
    """
    trades_T: list[dict] = []
    trades_B: list[dict] = []
    skip_reasons: Counter = Counter()

    for day in _iter_trading_days(*test_range):
        try:
            setups = get_setup(day)
        except Exception as e:
            logger.debug(f"router_wf: skip {day} setup_error={e!r}")
            skip_reasons["setup_error"] += 1
            continue

        if not setups:
            skip_reasons["empty_setup"] += 1
            continue

        # Apples-to-apples scope: a day is either evaluated on BOTH sides
        # or skipped on BOTH. We build the bucket lists FIRST (both sides),
        # then simulate. If any simulation step fails, we discard the day's
        # contribution to both sides.
        ts_945 = _ET.localize(datetime.combine(day, datetime.min.time())
                              .replace(hour=9, minute=45))

        day_T: list[dict] = []
        day_B: list[dict] = []
        day_failed = False

        # Per-day brokers — persist dedup state across setups, fresh each day.
        # Treatment and baseline brokers remain SEPARATE so T's opens don't
        # pollute B's dedup state (apples-to-apples is preserved).
        broker_T = _MockBroker()
        broker_B = _MockBroker()

        for setup in setups:
            structure = _strategy_to_structure(setup.strategy, setup.direction)
            if structure is STRATEGY_NOT_SUPPORTED:
                skip_reasons["strategy_not_supported"] += 1
                continue

            # Treatment: router with tier gate.
            buckets_T = _route_entry(setup, ts_945, broker_T)
            # Baseline: router with tier gate disabled.
            with _bypass_tier_gate():
                buckets_B = _route_entry(setup, ts_945, broker_B)

            for sd in buckets_T:
                outcome = get_pnl(day, setup, structure, sd["dte_bucket"])
                if outcome is None:
                    day_failed = True; break
                # Tag the trade dict for downstream window_stats.
                outcome.setdefault("strategy", setup.strategy)
                outcome.setdefault("dte_bucket", sd["dte_bucket"])
                day_T.append(outcome)
                broker_T.record_open(strategy=setup.strategy, dte_bucket=sd["dte_bucket"])
            if day_failed:
                break

            for sd in buckets_B:
                outcome = get_pnl(day, setup, structure, sd["dte_bucket"])
                if outcome is None:
                    day_failed = True; break
                outcome.setdefault("strategy", setup.strategy)
                outcome.setdefault("dte_bucket", sd["dte_bucket"])
                day_B.append(outcome)
                broker_B.record_open(strategy=setup.strategy, dte_bucket=sd["dte_bucket"])
            if day_failed:
                break

        if day_failed:
            skip_reasons["sim_failure"] += 1
            continue   # invariant: drop from BOTH sides

        trades_T.extend(day_T)
        trades_B.extend(day_B)

    stats = window_stats(trades_T, trades_B)
    return {
        "train_range":  train_range,
        "test_range":   test_range,
        "stats":        stats,
        "skip_reasons": dict(skip_reasons),
        "verdict":      window_verdict(stats),
    }


import json
from data.options_history import OptionsHistory


def _build_get_pnl(spy_intraday_cache: dict, options_history):
    """Closure factory: returns a get_pnl(day, setup, structure, dte_bucket)
    that consults a per-day cached spy_intraday DataFrame and the shared
    options_history client."""
    from data.intraday_data import get_stock_intraday

    def get_pnl(day, setup, structure, dte_bucket):
        spy = spy_intraday_cache.get(day)
        if spy is None:
            spy = get_stock_intraday("SPY", 5, "minute", day, day)
            spy_intraday_cache[day] = spy
        return simulate_short_dte_day(day, structure, dte_bucket,
                                       spy, options_history)
    return get_pnl


def run_walk_forward(start: date, end: date,
                     train_months: int = 6,
                     test_months: int = 3,
                     step_months: int = 1) -> dict:
    """Run all windows in [start, end] and return the aggregate report."""
    from backtests.router_setup_builder import build_historical_setup

    options_history = OptionsHistory()
    spy_cache: dict = {}
    get_pnl = _build_get_pnl(spy_cache, options_history)

    windows = list(generate_windows(start, end,
                                    train_months=train_months,
                                    test_months=test_months,
                                    step_months=step_months))
    logger.info(f"router_wf: running {len(windows)} windows from {start} to {end}")
    results = []
    for i, (train_range, test_range) in enumerate(windows, 1):
        logger.info(f"router_wf: window {i}/{len(windows)} test={test_range}")
        r = run_window(train_range=train_range, test_range=test_range,
                       get_setup=build_historical_setup, get_pnl=get_pnl)
        s = r["stats"]
        logger.info(
            f"router_wf: window {i} n_T={s['n_trades_T']} n_B={s['n_trades_B']} "
            f"ΔPnL/trade={s['delta_pnl_per_trade']:.2f} "
            f"ΔSharpe={s['delta_sharpe']:.2f} verdict={r['verdict']}"
        )
        results.append(r)

    agg = aggregate_verdict(results)
    return {"windows": results, "aggregate": agg}


if __name__ == "__main__":
    import argparse
    from datetime import datetime as _dt

    parser = argparse.ArgumentParser(description="Phase 3 entry-router WF backtest")
    parser.add_argument("--start", default="2024-01-02", help="ISO date")
    parser.add_argument("--end",   default="2025-12-31", help="ISO date")
    parser.add_argument("--out",   default="logs/router_wf_report.json")
    args = parser.parse_args()

    start_d = _dt.fromisoformat(args.start).date()
    end_d   = _dt.fromisoformat(args.end).date()

    report = run_walk_forward(start_d, end_d)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # Serialize: convert date tuples to ISO strings, Counters already dict.
    def _ser(obj):
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, tuple):
            return [_ser(x) for x in obj]
        return obj
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=_ser)
    logger.info(f"router_wf: wrote report to {args.out}")
    logger.info(f"router_wf: aggregate = {report['aggregate']}")
