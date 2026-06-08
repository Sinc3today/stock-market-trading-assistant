"""backtests/opening_range_study.py -- 0DTE opening-range signal study (Phase 1).

Tests whether the 15-min opening-range breakout (and the overnight gap) predicts
the REST-OF-DAY SPY direction, out-of-sample. This is the entry signal the bot's
existing (losing) 0DTE directional plays already use (intraday_backtest's OR+VWAP
blend) — so this isolates whether the SIGNAL is the problem or the structure/exit.

Underlying-only (no options). Reuses the dip-buy study's edge machinery: the
"forward return" is the rest-of-day return; the "trigger" is each OR/gap
condition. Research only.

Run: python -m backtests.opening_range_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.dipbuy_signal_study import edge_vs_baseline, per_year_edges

OR_MINUTES = 15          # opening range = first 15 min after the 09:30 open
ET = "US/Eastern"
ARMS = ("break_up", "break_down", "gap_up", "gap_down")


def _to_et(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if d.index.tz is None:
        d = d.tz_localize("UTC")
    return d.tz_convert(ET)


def daily_features(day_bars: pd.DataFrame, prior_close: float | None) -> dict | None:
    """Features for one ET session (5-min bars from 09:30). The opening range is
    the first OR_MINUTES; the 'decision' is the close of the first bar AFTER the
    range (a confirmed close above/below the range high/low — like the breakout
    transcript). rest_return = decision_close → session close."""
    reg = day_bars.between_time("09:30", "15:59")
    if len(reg) < 6:
        return None
    or_end = (pd.Timestamp(reg.index[0]).normalize()
              + pd.Timedelta(hours=9, minutes=30 + OR_MINUTES)).tz_localize(None)
    or_bars = reg[reg.index.tz_localize(None) < or_end]
    after   = reg[reg.index.tz_localize(None) >= or_end]
    if len(or_bars) == 0 or len(after) == 0:
        return None
    or_high = float(or_bars["high"].max())
    or_low  = float(or_bars["low"].min())
    open_px = float(reg["open"].iloc[0])
    decision_px = float(after["close"].iloc[0])   # confirmed close just after the OR
    day_close   = float(reg["close"].iloc[-1])
    rest_return = (day_close - decision_px) / decision_px * 100.0
    gap_pct = ((open_px - prior_close) / prior_close * 100.0) if prior_close else 0.0
    return {
        "rest_return": rest_return,
        "gap_pct":     gap_pct,
        "break_up":    bool(decision_px > or_high),
        "break_down":  bool(decision_px < or_low),
        "gap_up":      bool(gap_pct > 0),
        "gap_down":    bool(gap_pct < 0),
    }


def build_day_table(intraday_df: pd.DataFrame) -> pd.DataFrame:
    """Per-session feature table indexed by date: rest_return + arm booleans."""
    et = _to_et(intraday_df)
    reg = et.between_time("09:30", "15:59")
    rows, prior_close = [], None
    for day, g in reg.groupby(reg.index.date):
        f = daily_features(g, prior_close)
        if f is not None:
            rows.append({"date": pd.Timestamp(day), **f})
        prior_close = float(g["close"].iloc[-1])
    table = pd.DataFrame(rows).set_index("date")
    return table


def run_arm(table: pd.DataFrame, arm: str) -> dict:
    """Conditional rest-of-day return when `arm` fires, vs the unconditional
    baseline + per-year consistency. Sign tells momentum (same dir as break) vs
    fade (opposite)."""
    fwd  = table["rest_return"]
    trig = table[arm].astype(bool)
    stats = edge_vs_baseline(fwd, trig)
    pye   = per_year_edges(fwd, trig)
    return {"arm": arm, **stats, "per_year": pye}


def main():
    from data.intraday_data import get_stock_intraday
    from datetime import date
    df = get_stock_intraday("SPY", 5, "minute", date(2024, 6, 7), date(2026, 6, 6))
    table = build_day_table(df)
    n_days = len(table)
    base = float(table["rest_return"].mean())
    print(f"Opening-range 0DTE signal study — {n_days} sessions "
          f"(2024-06..2026-06); baseline rest-of-day return {base:+.3f}%\n")
    print(f"{'arm':>11}{'n':>5}{'cond%':>9}{'base%':>9}{'edge%':>9}{'pos%':>7}  interpretation")
    for arm in ARMS:
        r = run_arm(table, arm)
        # momentum if the conditional move continues the break direction
        bull = arm in ("break_up", "gap_up")
        cm = r["cond_mean"]
        interp = ("momentum (continue)" if (cm > 0) == bull and abs(cm) >= 0.05
                  else "fade (reverse)"  if (cm < 0) == bull and abs(cm) >= 0.05
                  else "no edge")
        print(f"{arm:>11}{r['n']:>5}{cm:>9.3f}{r['baseline_mean']:>9.3f}"
              f"{r['edge']:>9.3f}{r['pct_positive']:>6.0f}%  {interp}")


if __name__ == "__main__":
    main()
