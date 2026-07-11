"""backtests/gap_conditional_study.py -- weekend gaps + late-day 1DTE, conditioned.

User pushback (2026-07-10): "weekend gap averages zero" is an unconditional
mean — condition it on regime/vol/recency before trusting Friday entries. Same
skepticism for 1DTE entered late in the day (the trade IS the overnight gap).

Sample: SPY + VIX 2018-present via yfinance (includes Volmageddon-2018 and
COVID-2020 — our 5yr window is too calm to speak about high-vol weekends).

Part A: Fri-close -> Mon-open gap, bucketed by
  - Friday VIX level (<15, 15-20, 20-30, >30)
  - Friday's own day move (<-1%, -1..0, 0..1, >+1%)
  - trend state (SPY above/below 50-day MA)
  - era (2018-20 / 2021-23 / 2024+)
  Metrics that matter for a short-strike seller: P(gap breaches ±0.8%), worst.

Part B: the 1DTE-entered-at-close question — with 0.20-delta shorts sized off
Friday's VIX, what fraction of SHORT-STRIKE breaches happen AT THE OPEN (gap,
nothing you can do) vs intraday (watchdog can act)? By VIX bucket.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from signals.condor_calc import _strike_for_delta

BREACH = 0.8   # % — roughly where 0.20-delta short strikes sit at 1-2 DTE


def load():
    import yfinance as yf
    spy = yf.Ticker("SPY").history(start="2018-01-01", auto_adjust=True)
    spy.index = pd.to_datetime(spy.index).tz_localize(None)
    vix = yf.Ticker("^VIX").history(start="2018-01-01", auto_adjust=True)
    vix.index = pd.to_datetime(vix.index).tz_localize(None)
    df = pd.DataFrame({
        "open": spy["Open"], "close": spy["Close"],
        "high": spy["High"], "low": spy["Low"],
        "vix": vix["Close"].reindex(spy.index).ffill(),
    }).dropna()
    df["ma50"] = df["close"].rolling(50).mean()
    return df


def bucket_table(rows, key_fn, title):
    groups = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    print(f"\n== {title} ==")
    print(f"{'bucket':>16}{'n':>5}{'mean':>8}{'%pos':>6}{'P(<-0.8%)':>11}{'P(|g|>0.8%)':>12}{'worst':>8}")
    for k in sorted(groups, key=str):
        g = groups[k]
        gaps = [r["gap"] for r in g]
        n = len(gaps)
        if n < 5:
            continue
        neg = sum(1 for x in gaps if x < -BREACH) / n * 100
        big = sum(1 for x in gaps if abs(x) > BREACH) / n * 100
        print(f"{str(k):>16}{n:>5}{sum(gaps)/n:>7.3f}%{sum(1 for x in gaps if x>0)/n*100:>5.0f}%"
              f"{neg:>10.1f}%{big:>11.1f}%{min(gaps):>7.2f}%")


def vix_bucket(v):
    return "<15" if v < 15 else "15-20" if v < 20 else "20-30" if v < 30 else ">30"


def run():
    df = load()
    idx = list(df.index)
    weekends = []
    for i in range(1, len(idx)):
        if (idx[i] - idx[i - 1]).days < 3 or idx[i].weekday() != 0:
            continue                      # Monday after a full weekend only
        fri, mon = idx[i - 1], idx[i]
        f = df.loc[fri]
        prev_close = float(df.loc[idx[i - 2], "close"]) if i >= 2 else None
        weekends.append({
            "gap": (float(df.loc[mon, "open"]) - float(f["close"])) / float(f["close"]) * 100,
            "vix": float(f["vix"]),
            "fri_ret": (float(f["close"]) - prev_close) / prev_close * 100 if prev_close else 0.0,
            "trend": "above MA50" if float(f["close"]) > float(f["ma50"]) else "below MA50",
            "era": ("2018-20" if fri.year <= 2020 else
                    "2021-23" if fri.year <= 2023 else "2024+"),
        })
    print(f"weekends: {len(weekends)} (2018-present)")
    bucket_table(weekends, lambda r: vix_bucket(r["vix"]), "A1. by Friday VIX")
    bucket_table(weekends, lambda r: ("dn>1%" if r["fri_ret"] < -1 else
                                      "dn0-1%" if r["fri_ret"] < 0 else
                                      "up0-1%" if r["fri_ret"] < 1 else "up>1%"),
                 "A2. by Friday's own move")
    bucket_table(weekends, lambda r: r["trend"], "A3. by trend state")
    bucket_table(weekends, lambda r: r["era"], "A4. by era")

    # ── Part B: 1DTE entered at the close — where do breaches happen? ──
    print("\n== B. 1DTE condor at close: breach source by VIX bucket ==")
    print(f"{'vix':>8}{'n':>6}{'no breach':>11}{'AT OPEN (gap)':>15}{'intraday only':>15}")
    stats = {}
    for i in range(210, len(idx) - 2):
        d = idx[i]
        spot = float(df.loc[d, "close"])
        sigma = float(df.loc[d, "vix"]) / 100.0
        t = 2 / 365.0
        sc = _strike_for_delta("call", spot, t, sigma, 0.20)
        sp = _strike_for_delta("put", spot, t, sigma, 0.20)
        nxt = df.loc[idx[i + 1]]
        o, h, l = float(nxt["open"]), float(nxt["high"]), float(nxt["low"])
        b = stats.setdefault(vix_bucket(float(df.loc[d, "vix"])), {"n": 0, "open": 0, "intra": 0})
        b["n"] += 1
        if o <= sp or o >= sc:
            b["open"] += 1
        elif l <= sp or h >= sc:
            b["intra"] += 1
    for k in ("<15", "15-20", "20-30", ">30"):
        b = stats.get(k)
        if not b or b["n"] < 10:
            continue
        n = b["n"]
        print(f"{k:>8}{n:>6}{(n-b['open']-b['intra'])/n*100:>10.1f}%"
              f"{b['open']/n*100:>14.1f}%{b['intra']/n*100:>14.1f}%")


if __name__ == "__main__":
    run()
