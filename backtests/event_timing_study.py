"""backtests/event_timing_study.py -- event-relative timing ("buy rumor, sell news").

We currently SKIP event days (defense). The folk wisdom + our edges suggest a
testable pattern: SPY drifts UP into an event (rumor) and the post-event move
fades / IV crushes (news). Test whether SPY's pre- and post-event returns differ
from baseline around computable monthly events — NFP (1st Friday) and OPEX (3rd
Friday). If post-event reverts / vol crushes, there's an OFFENSE case (trade the
day-after) vs only defending.

Caveat: NFP/OPEX are MILDER than FOMC/CPI (the events the user cares most about),
which need a historical date table — a follow-up. So this is a lower bound on the
effect. Research only. Run: python -m backtests.event_timing_study
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.dipbuy_signal_study import forward_returns


def _nth_friday(year: int, month: int, n: int) -> pd.Timestamp:
    first = pd.Timestamp(year, month, 1)
    offset = (4 - first.weekday()) % 7          # weekday 4 == Friday
    return first + pd.Timedelta(days=offset) + pd.Timedelta(weeks=n - 1)


def event_dates(index: pd.DatetimeIndex, kind: str = "nfp") -> list:
    """Computable monthly event dates aligned to trading days in `index`.
    nfp = 1st Friday, opex = 3rd Friday. Aligns to the date or nearest prior bar."""
    n = 1 if kind == "nfp" else 3
    idx = pd.DatetimeIndex(index)
    out = []
    for p in pd.period_range(idx.min(), idx.max(), freq="M"):
        fri = _nth_friday(p.year, p.month, n)
        if fri in idx:
            out.append(fri)
        else:
            prior = idx[idx <= fri]
            if len(prior):
                out.append(prior[-1])
    return out


def event_window_returns(spy_df: pd.DataFrame, evt_dates, pre: int = 3, post: int = 3) -> dict:
    """Pre-event run-up (T−pre → T) and post-event reaction (T → T+post) %,
    vs the unconditional baseline forward `post`-day return."""
    close = spy_df["close"].astype(float)
    pos = {d: i for i, d in enumerate(close.index)}
    pre_r, post_r = [], []
    for d in evt_dates:
        i = pos.get(pd.Timestamp(d))
        if i is None or i - pre < 0 or i + post >= len(close):
            continue
        pre_r.append((close.iloc[i] / close.iloc[i - pre] - 1) * 100)
        post_r.append((close.iloc[i + post] / close.iloc[i] - 1) * 100)
    base = forward_returns(close, post)
    base_mean = float(base.dropna().mean())
    n = len(post_r)
    return {
        "n":            n,
        "pre_mean":     round(sum(pre_r) / len(pre_r), 4) if pre_r else 0.0,
        "post_mean":    round(sum(post_r) / n, 4) if n else 0.0,
        "post_pct_pos": round(sum(1 for x in post_r if x > 0) / n * 100, 1) if n else 0.0,
        "baseline":     round(base_mean, 4),
        "post_edge":    round((sum(post_r) / n - base_mean), 4) if n else 0.0,
    }


def _vix_crush(evt_dates) -> dict:
    """Avg VIX change T → T+1 around events (negative = post-event vol crush)."""
    vp = os.path.join(os.path.dirname(__file__), "vix_history.csv")
    vix = pd.read_csv(vp, index_col=0, parse_dates=True).sort_index()
    vix = vix[("value" if "value" in vix.columns else vix.columns[-1])].astype(float)
    pos = {d: i for i, d in enumerate(vix.index)}
    chg = []
    for d in evt_dates:
        i = pos.get(pd.Timestamp(d))
        if i is not None and i + 1 < len(vix):
            chg.append(float(vix.iloc[i + 1] - vix.iloc[i]))
    return {"n": len(chg), "mean_vix_change": round(sum(chg) / len(chg), 3) if chg else 0.0}


def main():
    from backtests.dipbuy_signal_study import load_spy
    spy = load_spy()
    print(f"Event-relative timing — SPY {spy.index.min().date()}..{spy.index.max().date()}")
    print("(pre = run-up into event; post = reaction; post_edge = post vs baseline)\n")
    print(f"{'event':>6}{'n':>5}{'pre%':>8}{'post%':>8}{'base%':>8}{'post_edge%':>12}{'postUp%':>8}{'ΔVIX':>7}")
    for kind in ("nfp", "opex"):
        ed = event_dates(spy.index, kind=kind)
        r  = event_window_returns(spy, ed, pre=3, post=3)
        vc = _vix_crush(ed)
        print(f"{kind:>6}{r['n']:>5}{r['pre_mean']:>8.3f}{r['post_mean']:>8.3f}"
              f"{r['baseline']:>8.3f}{r['post_edge']:>12.3f}{r['post_pct_pos']:>7.0f}%"
              f"{vc['mean_vix_change']:>7.2f}")


if __name__ == "__main__":
    main()
