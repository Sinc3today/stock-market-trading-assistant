"""backtests/condor_breach_study.py -- can a vol-expansion signal predict the
calm iron condor's BREACH (losing) days?

The calm condor wins 82% (265 days, 47 losers in the 5yr backtest). The losers
are breaches — a bigger-than-expected move on a supposedly-calm day. If a
grounded vol-expansion early-warning flags them, a filter that skips those days
lifts win-rate + P&L. Tests (one clean definition each, economically grounded):
  - VIX rising      (VIX > VIX 5d ago — vol picking up)
  - backwardation   (VIX9D > VIX3M — acute near-term stress)
  - VVIX rising     (vol-of-vol > its 60d MA — fragility building)

Note: daily backtest = synthetic-payoff model (a 'loss' = SPY broke the range),
fine for "do breaches cluster by signal". Research only.
Run: python -m backtests.condor_breach_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

_DIR = os.path.dirname(__file__)


def _load_series(name: str) -> pd.Series:
    df = pd.read_csv(os.path.join(_DIR, f"{name}.csv"), index_col=0, parse_dates=True).sort_index()
    col = "value" if "value" in df.columns else df.columns[-1]
    return df[col].astype(float)


def _at(series: pd.Series, d) -> float:
    d = pd.Timestamp(d)
    return float(series.reindex(series.index.union([d])).ffill().get(d, float("nan")))


def attach_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Add vol-expansion signal columns to the backtest day table (needs date+vix)."""
    d = df.sort_values("date").reset_index(drop=True).copy()
    d["vix_rising"] = d["vix"] > d["vix"].shift(5)
    v9, v3 = _load_series("vix9d_history"), _load_series("vix3m_history")
    vvix = _load_series("vvix_history")
    vvix_ma = vvix.rolling(60).mean()
    d["backwardation"] = [(_at(v9, x) > _at(v3, x)) for x in d["date"]]
    d["vvix_rising"]   = [(_at(vvix, x) > _at(vvix_ma, x)) for x in d["date"]]
    return d


def breach_split(df: pd.DataFrame, signal_col: str) -> dict:
    """Split calm-condor (choppy_low_vol, tradeable) days by `signal_col`.
    sig_false == the filtered book if we skip signal-true days."""
    d = df[(df["regime"] == "choppy_low_vol") & (df["tradeable"] == True)].copy()
    d = d[d["outcome"].isin(["win", "loss", "breakeven"])]
    d["year"] = [pd.Timestamp(x).year for x in d["date"]]

    def stats(sub):
        n = len(sub)
        return {
            "n":        n,
            "win_rate": round((sub["outcome"] == "win").mean(), 3) if n else 0.0,
            "pnl":      int(sub["pnl"].sum()),
            "mean":     round(sub["pnl"].mean(), 1) if n else 0.0,
        }
    out = {"sig_true":  stats(d[d[signal_col] == True]),
           "sig_false": stats(d[d[signal_col] == False])}
    out["per_year"] = {}
    for y, g in d.groupby("year"):
        out["per_year"][int(y)] = {
            "true_loss":  int((g[g[signal_col] == True]["outcome"] == "loss").sum()),
            "false_loss": int((g[g[signal_col] == False]["outcome"] == "loss").sum()),
        }
    return out


def main():
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar
    s, v = BacktestDataLoader().load(years=5, source="local")
    df = SPYBacktest(s, v, EventCalendar(), years=5).run()
    df = attach_signals(df)
    base = df[(df["regime"] == "choppy_low_vol") & (df["tradeable"] == True)]
    base = base[base["outcome"].isin(["win", "loss", "breakeven"])]
    nloss = int((base["outcome"] == "loss").sum())
    print(f"Calm condor breach-prediction — {len(base)} days, {nloss} breaches (losers)\n")
    print(f"{'signal':>14}{'bucket':>10}{'n':>5}{'win%':>7}{'losses':>8}{'pnl':>9}")
    for sig in ("vix_rising", "backwardation", "vvix_rising"):
        r = breach_split(df, sig)
        for b in ("sig_true", "sig_false"):
            s2 = r[b]
            losses = int(round((1 - s2["win_rate"]) * s2["n"]))
            tag = "FLAG/skip" if b == "sig_true" else "keep"
            print(f"{sig if b=='sig_true' else '':>14}{tag:>10}{s2['n']:>5}"
                  f"{s2['win_rate']*100:>6.0f}%{losses:>8}{s2['pnl']:>9}")
        # how much of the breaches did the signal catch?
        caught = sum(d2["true_loss"] for d2 in r["per_year"].values())
        print(f"{'':>14}{'→ breaches flagged':>10} {caught}/{nloss}\n")


if __name__ == "__main__":
    main()
