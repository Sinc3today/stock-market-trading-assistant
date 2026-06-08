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


def daily_features(day_bars: pd.DataFrame, prior_close: float | None,
                   or_minutes: int = OR_MINUTES) -> dict | None:
    """Features for one ET session (5-min bars from 09:30). The opening range is
    the first `or_minutes`; the 'decision' is the close of the first bar AFTER the
    range (a confirmed close above/below the range high/low — like the breakout
    transcript). Adds a session VWAP filter through the decision bar (the bot's
    real entry blend is OR-break AND on the right side of VWAP).
    rest_return = decision_close → session close."""
    reg = day_bars.between_time("09:30", "15:59")
    if len(reg) < 6:
        return None
    idx_naive = reg.index.tz_localize(None)
    or_end = (pd.Timestamp(reg.index[0]).normalize()
              + pd.Timedelta(hours=9, minutes=30 + or_minutes)).tz_localize(None)
    or_bars = reg[idx_naive < or_end]
    after   = reg[idx_naive >= or_end]
    if len(or_bars) == 0 or len(after) == 0:
        return None
    or_high = float(or_bars["high"].max())
    or_low  = float(or_bars["low"].min())
    open_px = float(reg["open"].iloc[0])
    decision_px   = float(after["close"].iloc[0])   # confirmed close just after the OR
    decision_time = pd.Timestamp(after.index[0]).tz_localize(None)
    day_close   = float(reg["close"].iloc[-1])
    rest_return = (day_close - decision_px) / decision_px * 100.0
    gap_pct = ((open_px - prior_close) / prior_close * 100.0) if prior_close else 0.0
    # session VWAP through the decision bar (typical price * volume)
    upto = reg[idx_naive <= decision_time]
    tp   = (upto["high"] + upto["low"] + upto["close"]) / 3.0
    vol  = upto["volume"].replace(0, 1.0)
    vwap = float((tp * vol).sum() / vol.sum())
    above_vwap = decision_px > vwap
    break_up   = decision_px > or_high
    break_down = decision_px < or_low
    return {
        "rest_return":    rest_return,
        "gap_pct":        gap_pct,
        "break_up":       bool(break_up),
        "break_down":     bool(break_down),
        "gap_up":         bool(gap_pct > 0),
        "gap_down":       bool(gap_pct < 0),
        "above_vwap":     bool(above_vwap),
        "break_up_vwap":  bool(break_up and above_vwap),
        "break_down_vwap": bool(break_down and not above_vwap),
    }


def build_day_table(intraday_df: pd.DataFrame, or_minutes: int = OR_MINUTES) -> pd.DataFrame:
    """Per-session feature table indexed by date: rest_return + arm booleans."""
    et = _to_et(intraday_df)
    reg = et.between_time("09:30", "15:59")
    rows, prior_close = [], None
    for day, g in reg.groupby(reg.index.date):
        f = daily_features(g, prior_close, or_minutes=or_minutes)
        if f is not None:
            rows.append({"date": pd.Timestamp(day), **f})
        prior_close = float(g["close"].iloc[-1])
    table = pd.DataFrame(rows).set_index("date")
    return table


def vix_gate(table: pd.DataFrame, arm: str, threshold: float = 18.0) -> dict:
    """Split an arm's conditional rest-of-day edge by VIX bucket (calm < threshold
    vs high >= threshold). Tests whether small-TF directional only pays when there
    is enough range to move. Requires a 'vix' column on `table`."""
    out = {}
    for label, mask in (("calm", table["vix"] < threshold),
                        ("high", table["vix"] >= threshold)):
        sub = table[mask]
        cond = sub.loc[sub[arm].astype(bool), "rest_return"]
        n = int(len(cond))
        out[label] = {
            "n":         n,
            "cond_mean": round(float(cond.mean()), 4) if n else 0.0,
            "pct_positive": round(float((cond > 0).mean()) * 100, 1) if n else 0.0,
        }
    return out


def run_arm(table: pd.DataFrame, arm: str) -> dict:
    """Conditional rest-of-day return when `arm` fires, vs the unconditional
    baseline + per-year consistency. Sign tells momentum (same dir as break) vs
    fade (opposite)."""
    fwd  = table["rest_return"]
    trig = table[arm].astype(bool)
    stats = edge_vs_baseline(fwd, trig)
    pye   = per_year_edges(fwd, trig)
    return {"arm": arm, **stats, "per_year": pye}


def sweep_or_windows(intraday_df: pd.DataFrame, windows=(5, 15, 30, 60),
                     arms=("break_up", "break_down")) -> dict:
    """{window: {arm: run_arm result}} — tests whether the OR-breakout edge is
    stable across the (arbitrary) window choice. Sign-flips across windows = noise."""
    out = {}
    for w in windows:
        t = build_day_table(intraday_df, or_minutes=w)
        out[w] = {a: run_arm(t, a) for a in arms}
    return out


def attach_vix(table: pd.DataFrame, vix_path: str | None = None) -> pd.DataFrame:
    """Add a 'vix' column to a day table (prior-or-same-day VIX close, ffilled)."""
    vix_path = vix_path or os.path.join(os.path.dirname(__file__), "vix_history.csv")
    vix = pd.read_csv(vix_path, index_col=0, parse_dates=True)
    col = "value" if "value" in vix.columns else vix.columns[0]
    s = vix[col].sort_index()
    t = table.copy()
    t["vix"] = [float(s.reindex(s.index.union([d])).ffill().get(d, float("nan")))
                for d in t.index]
    return t


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

    print("\nOR-window stability (break_up cond-mean %, sign-flip across windows = noise):")
    sweep = sweep_or_windows(df)
    for w, arms in sweep.items():
        print(f"  {w:>2}-min: break_up {arms['break_up']['cond_mean']:+.3f}%  "
              f"break_down {arms['break_down']['cond_mean']:+.3f}%")

    print("\nVIX gate (15-min OR — does directional pay more with range?):")
    tv = attach_vix(table)
    for arm in ("break_up", "break_down"):
        g = vix_gate(tv, arm, threshold=18.0)
        print(f"  {arm:>11}: calm<18 n={g['calm']['n']} {g['calm']['cond_mean']:+.3f}%  | "
              f"high>=18 n={g['high']['n']} {g['high']['cond_mean']:+.3f}%")


if __name__ == "__main__":
    main()
