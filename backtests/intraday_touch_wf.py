"""
backtests/intraday_touch_wf.py -- Walk-forward comparison: intraday-touch exit
vs daily-close exit on the realistic-priced SPY backtest population.

Runs simulate_trade twice on identical entry days (touch off, touch on),
joins per-trade outcomes on date, splits 60/40 by entry date (early =
in-sample, late = out-of-sample), and aggregates Δ$/trade, attribution %, and
per-regime breakdown. A later task adds the six-preset verdict matrix on top.

Run:  python -m backtests.intraday_touch_wf
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config


def split_oos(trades: pd.DataFrame, fraction: float = 0.6) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split trades chronologically: first `fraction` = in-sample, rest = OOS."""
    df = trades.sort_values("date").reset_index(drop=True)
    cut = int(len(df) * fraction)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def compare_runs(trades_off: pd.DataFrame, trades_on: pd.DataFrame,
                 oos_fraction: float = 0.6) -> dict:
    """Compute Δ$/trade, attribution %, IS/OOS, per-regime breakdown.

    trades_off / trades_on each have columns: date, regime, pnl_dollars,
    exit_reason. They share entry dates 1:1; we inner-join on date.
    """
    off = trades_off.set_index("date")[["pnl_dollars", "regime", "exit_reason"]].rename(
        columns={"pnl_dollars": "pnl_off", "exit_reason": "reason_off"})
    on = trades_on.set_index("date")[["pnl_dollars", "exit_reason"]].rename(
        columns={"pnl_dollars": "pnl_on", "exit_reason": "reason_on"})
    j = off.join(on, how="inner").reset_index()
    j["delta"] = j["pnl_on"] - j["pnl_off"]

    ins, oos = split_oos(j, fraction=oos_fraction)
    attribution = float((oos["reason_on"] == "target_intraday").mean()) if len(oos) else 0.0

    per_regime = {}
    for r in sorted(j["regime"].unique()):
        sub = j[j["regime"] == r]
        per_regime[r] = {"n": int(len(sub)),
                         "delta_per_trade": float(sub["delta"].mean()) if len(sub) else 0.0}

    return {
        "n_total":                int(len(j)),
        "n_is":                   int(len(ins)),
        "n_oos":                  int(len(oos)),
        "is_delta_per_trade":     float(ins["delta"].mean()) if len(ins) else 0.0,
        "oos_delta_per_trade":    float(oos["delta"].mean()) if len(oos) else 0.0,
        "oos_baseline_per_trade": float(oos["pnl_off"].mean()) if len(oos) else 0.0,
        "oos_attribution":        attribution,
        "per_regime":             per_regime,
    }


PRESETS = [
    {"name": "strict-3σ",          "stat_floor": 40.0, "scale_floor": 0.15, "attrib_floor": 0.25, "is_sanity": True},
    {"name": "default-2σ",         "stat_floor": config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR,
                                   "scale_floor": config.INTRADAY_TOUCH_SHIP_MIN_FRAC,
                                   "attrib_floor": config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB,
                                   "is_sanity": True},
    {"name": "lenient-1.5σ",       "stat_floor": 15.0, "scale_floor": 0.05, "attrib_floor": 0.10, "is_sanity": True},
    {"name": "research-1σ",        "stat_floor": 10.0, "scale_floor": 0.05, "attrib_floor": 0.05, "is_sanity": False},
    {"name": "attribution-strict", "stat_floor": 20.0, "scale_floor": 0.10, "attrib_floor": 0.30, "is_sanity": True},
    {"name": "oos-only",           "stat_floor": config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR,
                                   "scale_floor": config.INTRADAY_TOUCH_SHIP_MIN_FRAC,
                                   "attrib_floor": config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB,
                                   "is_sanity": False},
]


def evaluate_preset(measured: dict, preset: dict) -> dict:
    """Check a measured-result dict against one preset's four floors.

    Returns {"ship": bool, "floors_met": {dollar, scale, attrib, is_sanity: bool}}.
    The scale floor is computed as a fraction of |baseline| so a near-zero
    baseline doesn't make the floor vacuous; if baseline is effectively zero
    we treat the scale gate as met (the dollar floor is the meaningful gate).
    """
    delta    = measured["oos_delta_per_trade"]
    baseline = measured["oos_baseline_per_trade"]
    attrib   = measured["oos_attribution"]
    is_delta = measured["is_delta_per_trade"]

    dollar_ok = delta >= preset["stat_floor"]
    if abs(baseline) > 1e-9:
        scale_ok = (delta / abs(baseline)) >= preset["scale_floor"]
    else:
        scale_ok = True   # baseline ~ 0; dollar floor is the only meaningful gate
    attrib_ok = attrib >= preset["attrib_floor"]
    is_ok     = (is_delta > 0) if preset["is_sanity"] else True

    return {
        "ship": dollar_ok and scale_ok and attrib_ok and is_ok,
        "floors_met": {"dollar": dollar_ok, "scale": scale_ok,
                       "attrib": attrib_ok, "is_sanity": is_ok},
    }


def print_verdict_matrix(measured: dict) -> dict:
    """Print the measured result + verdict for each of the 6 presets.
    Returns {preset_name: ship_bool} for callers that want the verdicts."""
    print("\n" + "=" * 78)
    print("  INTRADAY-TOUCH EXIT — WALK-FORWARD VERDICT")
    print("=" * 78)
    print(f"  n_OOS = {measured['n_oos']}   n_IS = {measured['n_is']}")
    pct_of_baseline = (measured['oos_delta_per_trade'] / abs(measured['oos_baseline_per_trade']) * 100
                       if abs(measured['oos_baseline_per_trade']) > 1e-9 else 0.0)
    print(f"  measured:  IS Δ=${measured['is_delta_per_trade']:+.1f}/trade   "
          f"OOS Δ=${measured['oos_delta_per_trade']:+.1f}/trade  "
          f"({pct_of_baseline:+.1f}% of baseline)")
    print(f"             attribution = {measured['oos_attribution']*100:.1f}% of OOS exits via target_intraday")
    print(f"\n  {'preset':<20} {'dollar':>7} {'scale':>6} {'attrib':>7} {'IS':>4}   verdict")
    print("-" * 78)
    verdicts = {}
    for p in PRESETS:
        out = evaluate_preset(measured, p)
        f   = out["floors_met"]
        mark = lambda b: "✓" if b else "✗"
        binding = "  <- BINDING" if p["name"] == "default-2σ" else ""
        print(f"  {p['name']:<20} {mark(f['dollar']):>7} {mark(f['scale']):>6} "
              f"{mark(f['attrib']):>7} {mark(f['is_sanity']):>4}   "
              f"{'SHIP' if out['ship'] else 'no'}{binding}")
        verdicts[p["name"]] = out["ship"]

    print("\n  Per-regime Δ$/trade:")
    for r, info in measured["per_regime"].items():
        print(f"    {r:<22} n={info['n']:>4}   Δ=${info['delta_per_trade']:+.1f}/trade")
    print("=" * 78 + "\n")
    return verdicts


def _price_population(spy_df, vix_df, regime_df, intraday_touch: bool) -> pd.DataFrame:
    """Price every tradeable regime day with the given mode; return per-trade rows."""
    from backtests.realistic_pricing import simulate_trade, _vix_lookup

    spy = spy_df.copy(); spy.index = pd.to_datetime(spy.index)
    dates = sorted(pd.to_datetime(spy.index))
    didx  = {d: i for i, d in enumerate(dates)}
    va    = _vix_lookup(dates, vix_df)

    rows = []
    for _, r in regime_df[regime_df["tradeable"] == True].iterrows():
        d = pd.to_datetime(r["date"])
        if d not in didx:
            continue
        play = r["play"]
        if play == "skip":
            continue
        out = simulate_trade(spy, dates, didx[d], play, va, intraday_touch=intraday_touch)
        if out is None:
            continue
        out["date"]   = d
        out["regime"] = r["regime"]
        rows.append(out)
    return pd.DataFrame(rows)


def run() -> dict:
    """Load 5yr data, price both modes, return the comparison dict. (No printing.)"""
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar

    spy_df, vix_df = BacktestDataLoader().load(years=5, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()

    logger.info("Pricing daily-close mode (touch off)...")
    off = _price_population(spy_df, vix_df, regime_df, intraday_touch=False)
    logger.info(f"  {len(off)} trades")
    logger.info("Pricing intraday-touch mode (touch on)...")
    on  = _price_population(spy_df, vix_df, regime_df, intraday_touch=True)
    logger.info(f"  {len(on)} trades")

    return compare_runs(off, on)


def main():
    result = run()
    print_verdict_matrix(result)


if __name__ == "__main__":
    main()
