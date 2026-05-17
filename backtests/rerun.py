"""
backtests/rerun.py -- One-shot: refresh history → backtest → save summary.

Wraps `backtests.spy_daily_backtest` so the /backtest dashboard can be
refreshed in one command. Writes the result to
`logs/backtest_summary.json`, which `data/backtest_summary.py` reads
on every dashboard hit.

Usage:
    python -m backtests.rerun                    # refresh + run 5y
    python -m backtests.rerun --years 3
    python -m backtests.rerun --skip-refresh     # reuse current CSV
    python -m backtests.rerun --no-save          # print only, don't update dashboard

Prints a delta line comparing the new run to whatever the dashboard
currently shows, so the user can see whether re-running materially
shifted the Sharpe / win-rate baseline.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from loguru import logger

import config
from data.backtest_summary import production_stats, save_production_stats


REGIME_NOTES = {
    "choppy_low_vol":     "Iron condor edge — the core profit driver.",
    "trending_down_calm": "Bear debit spread, modest edge.",
    "trending_up_calm":   "Bull debit spread, weak edge.",
    "trending_high_vol":  "Confirmed no edge — skipped in production.",
    "choppy_high_vol":    "Choppy with elevated VIX — typically skipped.",
}


# ── SUMMARY BUILDER ───────────────────────────────────

def compute_summary(df: pd.DataFrame, years: int, source: str) -> dict:
    """
    Compress a SPYBacktest results DataFrame into the dict shape that
    `data/backtest_summary.py:production_stats()` returns.
    """
    if df is None or df.empty:
        return {
            "source":  f"rerun_cli ({source})",
            "version": date.today().isoformat(),
            "years":   years,
            "overview": {"sharpe": 0.0, "win_rate_pct": 0.0,
                         "total_return_pct": None,
                         "trade_days": 0, "skip_days": 0},
            "by_regime":  [],
            "thresholds": _current_thresholds(),
        }

    traded   = df[df["tradeable"] == True]
    skipped  = df[df["tradeable"] == False]
    closed   = traded[traded["outcome"].isin(["win", "loss", "breakeven"])]
    wins     = closed[closed["outcome"] == "win"]

    win_rate = round(len(wins) / len(closed) * 100, 1) if len(closed) else 0.0
    daily    = traded["pnl"].values
    sharpe   = (float(np.mean(daily)) / (float(np.std(daily)) + 1e-9)) * (252 ** 0.5)
    total    = int(traded["pnl"].sum())

    by_regime: list[dict] = []
    for regime in sorted(df["regime"].unique()):
        rdf = df[df["regime"] == regime]
        rt  = rdf[rdf["tradeable"] == True]
        rc  = rt[rt["outcome"].isin(["win", "loss", "breakeven"])]
        rw  = rc[rc["outcome"] == "win"]
        wrp = round(len(rw) / len(rc) * 100, 1) if len(rc) else 0.0
        by_regime.append({
            "regime":       regime,
            "win_rate_pct": wrp,
            "tradeable":    bool(len(rt) > 0),
            "note":         REGIME_NOTES.get(regime, ""),
        })

    return {
        "source":   f"rerun_cli ({source})",
        "version":  date.today().isoformat(),
        "years":    years,
        "overview": {
            "sharpe":           round(float(sharpe), 2),
            "win_rate_pct":     win_rate,
            "total_return_pct": None,        # not modeled — fixed $/trade payouts
            "trade_days":       int(len(traded)),
            "skip_days":        int(len(skipped)),
            "total_pnl":        total,
        },
        "by_regime":  by_regime,
        "thresholds": _current_thresholds(),
    }


def _current_thresholds() -> dict:
    """Read live thresholds out of the regime detector module."""
    try:
        import signals.regime_detector as rd
        return {
            "ADX_TREND_MIN": float(rd.ADX_TREND_MIN),
            "VIX_CALM_MAX":  float(rd.VIX_CALM_MAX),
            "IC_RANGE_PCT":  float(getattr(rd, "IC_RANGE_PCT", 2.5)),
        }
    except Exception:
        return {}


# ── DELTA FORMATTER ───────────────────────────────────

def format_deltas(old: dict, new: dict) -> str:
    """Pretty-print Sharpe / win-rate / trade-day deltas between two summaries."""
    o, n = old.get("overview", {}), new.get("overview", {})
    def arrow(a, b):
        if a is None or b is None:
            return "—"
        d = b - a
        sign = "+" if d >= 0 else "−"
        return f"{sign}{abs(d):.2f}"
    lines = [
        "  ── Δ vs prior summary ─────────────────────",
        f"   Source prior : {old.get('source','?')}",
        f"   Sharpe       : {o.get('sharpe','?')} → {n.get('sharpe','?')}  ({arrow(o.get('sharpe'), n.get('sharpe'))})",
        f"   Win rate %   : {o.get('win_rate_pct','?')} → {n.get('win_rate_pct','?')}  ({arrow(o.get('win_rate_pct'), n.get('win_rate_pct'))})",
        f"   Trade days   : {o.get('trade_days','?')} → {n.get('trade_days','?')}",
    ]
    return "\n".join(lines)


# ── ORCHESTRATOR ──────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Refresh + re-run SPY daily backtest")
    p.add_argument("--years",        type=int, default=5)
    p.add_argument("--skip-refresh", action="store_true",
                   help="Skip the Polygon CSV refresh; use current spy_history.csv")
    p.add_argument("--no-save",      action="store_true",
                   help="Print results but don't update logs/backtest_summary.json")
    args = p.parse_args(argv)

    if not args.skip_refresh:
        from backtests.refresh_history import main as refresh_main
        print("▶ Refreshing SPY history from Polygon …")
        rc = refresh_main(["--years", str(args.years)])
        if rc != 0:
            print("✗ Refresh failed — continuing with existing CSV")
    else:
        print("▶ Skipping CSV refresh (--skip-refresh)")

    print(f"▶ Running SPYBacktest ({args.years}y, local CSV) …")
    # Imported here so a refresh-only invocation doesn't pay the import cost.
    from backtests.spy_daily_backtest import (
        BacktestDataLoader, SPYBacktest, print_report,
    )
    from data.event_calendar import EventCalendar
    loader = BacktestDataLoader()
    spy_df, vix_df = loader.load(years=args.years, source="local")
    cal = EventCalendar()
    bt  = SPYBacktest(spy_df, vix_df, cal, years=args.years)
    df  = bt.run()
    print_report(df, years=args.years, source="local")

    summary = compute_summary(df, years=args.years, source="local")
    prior   = production_stats()
    print(format_deltas(prior, summary))

    if args.no_save:
        print("\n  (--no-save; logs/backtest_summary.json untouched)")
        return 0

    save_production_stats(summary)
    path = os.path.join(config.LOG_DIR, "backtest_summary.json")
    print(f"\n✓ Saved fresh summary to {path}")
    print("  /backtest dashboard will reflect this on next page load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
