"""backtests/directional_spread_study.py -- what makes a TRENDING_UP_CALM
directional spread profitable?

User question (2026-07-13): we're in trending_up_calm (ADX>=32, VIX<18, above
200MA). Which entry signals/conditions make the regime's directional spread a
GOOD trade vs a coin flip?

Method (house discipline — falsification-first, one signal at a time, no combo
mining):
  1. Reconstruct the regime historically (2018-present, yfinance SPY+VIX)
     using the SAME rules as signals/regime_detector.py: rolling Wilder ADX(14)
     >= 32, VIX < 18, close > 200MA.
  2. On every qualifying day, model BOTH live structures at 45DTE:
       - bull PUT credit spread  (sell 0.40-delta put, $5 wing) — what the
         engine actually opened on 06-29 (trade 8EE266D2)
       - bull CALL debit spread  (buy ~0.55-delta, sell ~0.30-delta call) —
         what the regime card *says* ("BULL CALL DEBIT SPREAD")
     Priced with the project's r=0 Black-Scholes (learning.exit_manager.bs_price),
     sigma = that day's VIX/100. Managed like live: 70% profit target,
     close at 21 DTE, settle intrinsic at expiry. Daily close marks.
  3. Bucket entry days by ONE signal at a time, report n / win% / avg / total.
     Buckets with n<30 are printed but flagged; conclusions need n>=30.
  4. OOS honesty: headline buckets re-reported split 2018-2022 vs 2023+.

This is a STUDY — no live behavior changes. Doc: docs/DIRECTIONAL_SPREAD_STUDY.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from learning.exit_manager import bs_price
from signals.condor_calc import _strike_for_delta

ADX_MIN, VIX_CALM = 32.0, 18.0
DTE, CLOSE_DTE, TARGET = 45, 21, 0.70
WING = 5.0


def load():
    import yfinance as yf
    spy = yf.Ticker("SPY").history(start="2017-06-01", auto_adjust=True)
    spy.index = pd.to_datetime(spy.index).tz_localize(None)
    vix = yf.Ticker("^VIX").history(start="2017-06-01", auto_adjust=True)
    vix.index = pd.to_datetime(vix.index).tz_localize(None)
    df = pd.DataFrame({
        "open": spy["Open"], "high": spy["High"], "low": spy["Low"],
        "close": spy["Close"],
        "vix": vix["Close"].reindex(spy.index).ffill(),
    }).dropna()
    return df


def rolling_adx(df, period=14):
    """Wilder ADX(14) as a SERIES — same arithmetic as
    regime_detector._compute_adx (rolling-mean variant), vectorized."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm = plus_dm.where(plus_dm >= minus_dm, 0.0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)
    tr = pd.concat([(high - low), (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    return dx.rolling(period).mean()


def add_features(df):
    df = df.copy()
    df["adx"] = rolling_adx(df)
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    df["ext_pct"] = (df["close"] / df["ma200"] - 1) * 100
    df["ma20_dist"] = (df["close"] / df["ma20"] - 1) * 100
    df["don_hi20"] = df["close"].rolling(20).max()
    df["ret3"] = df["close"].pct_change(3) * 100
    up = (df["close"].diff() > 0).astype(int)
    # consecutive up closes ending today
    streak, s = [], 0
    for u in up:
        s = s + 1 if u else 0
        streak.append(s)
    df["up_streak"] = streak
    df["regime"] = (df["adx"] >= ADX_MIN) & (df["vix"] < VIX_CALM) & \
                   (df["close"] > df["ma200"])
    # trend age: days since the regime flag turned on
    age, a = [], 0
    for r in df["regime"]:
        a = a + 1 if r else 0
        age.append(a)
    df["trend_age"] = age
    return df.dropna(subset=["adx", "ma200", "rsi"])


def spread_pnl(df, i, kind):
    """Simulate one 45DTE bull spread entered at close of df.index[i].
    Returns dict(pnl, days_held, exit_reason) or None if unpriceable."""
    idx = df.index
    spot = float(df["close"].iloc[i])
    sigma = float(df["vix"].iloc[i]) / 100.0
    t0 = DTE / 365.0
    if kind == "credit":                      # bull put credit spread
        short_k = _strike_for_delta("put", spot, t0, sigma, 0.40)
        long_k = short_k - WING
        entry = bs_price("put", spot, short_k, t0, sigma) \
            - bs_price("put", spot, long_k, t0, sigma)      # net credit
        max_profit = entry * 100
        legs = (("put", short_k, -1), ("put", long_k, +1))
    else:                                     # bull call debit spread
        long_k = _strike_for_delta("call", spot, t0, sigma, 0.55)
        short_k = _strike_for_delta("call", spot, t0, sigma, 0.30)
        entry = bs_price("call", spot, long_k, t0, sigma) \
            - bs_price("call", spot, short_k, t0, sigma)    # net debit
        max_profit = (short_k - long_k - entry) * 100
        legs = (("call", long_k, +1), ("call", short_k, -1))
    if entry <= 0 or max_profit <= 0:
        return None

    exp_date = idx[i] + pd.Timedelta(days=DTE)      # real calendar expiry
    pnl = 0.0
    for j in range(i + 1, len(idx)):
        days_left = (exp_date - idx[j]).days        # true calendar DTE
        if days_left < 0:
            break
        t = days_left / 365.0
        s = float(df["close"].iloc[j])
        sg = float(df["vix"].iloc[j]) / 100.0
        val = sum(sign * bs_price(opt, s, k, t, sg) for opt, k, sign in legs)
        if kind == "credit":
            pnl = (entry + val) * 100     # val = -(cost to close the shorts)
        else:
            pnl = (val - entry) * 100
        if pnl >= TARGET * max_profit:
            return {"pnl": TARGET * max_profit, "days": j - i, "reason": "target"}
        if days_left <= CLOSE_DTE:        # live rule: close at 21 DTE
            return {"pnl": pnl, "days": j - i, "reason": "dte"}
    # ran off the end of available data — mark at last close (recent entries)
    return {"pnl": pnl, "days": j - i, "reason": "eod_data"}


def bucket_report(rows, key_fn, title, min_n=30):
    groups = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    print(f"\n-- {title} --")
    print(f"{'bucket':>14}{'n':>5}{'win%':>7}{'avg':>9}{'total':>10}{'worst':>9}")
    for k in sorted(groups, key=str):
        g = groups[k]
        p = [r["pnl"] for r in g]
        n = len(p)
        flag = "" if n >= min_n else "  (n<30 — no conclusions)"
        print(f"{str(k):>14}{n:>5}{sum(1 for x in p if x > 0)/n*100:>6.0f}%"
              f"{sum(p)/n:>9.2f}{sum(p):>10.0f}{min(p):>9.0f}{flag}")


def run():
    df = add_features(load())
    days = [i for i in range(len(df) - 1)
            if bool(df["regime"].iloc[i]) and df.index[i].year >= 2018]
    print(f"trending_up_calm days 2018+: {len(days)} "
          f"(of {sum(df.index.year >= 2018)} trading days)")

    for kind in ("credit", "debit"):
        rows = []
        for i in days:
            r = spread_pnl(df, i, kind)
            if r is None:
                continue
            d = df.iloc[i]
            rows.append({
                **r,
                "date": df.index[i],
                "ext": float(d["ext_pct"]), "ma20d": float(d["ma20_dist"]),
                "rsi": float(d["rsi"]), "age": int(d["trend_age"]),
                "vix": float(d["vix"]), "streak": int(d["up_streak"]),
                "don": bool(d["close"] >= d["don_hi20"]),
                "ret3": float(d["ret3"]), "dow": df.index[i].strftime("%a"),
                "era": "2018-22" if df.index[i].year <= 2022 else "2023+",
            })
        label = ("BULL PUT CREDIT (sell 0.40Δ, $5 wing)" if kind == "credit"
                 else "BULL CALL DEBIT (buy 0.55Δ / sell 0.30Δ)")
        p = [r["pnl"] for r in rows]
        print(f"\n==== {label} — 45DTE, 70% target, close at 21 DTE ====")
        print(f"BASELINE: n={len(p)} win {sum(1 for x in p if x>0)/len(p)*100:.0f}% "
              f"avg ${sum(p)/len(p):.2f} total ${sum(p):.0f} worst ${min(p):.0f}")
        bucket_report(rows, lambda r: r["era"], "era (the OOS axis)")
        bucket_report(rows, lambda r: ("<5%" if r["ext"] < 5 else "5-7%" if r["ext"] < 7
                                       else "7-9%" if r["ext"] < 9 else ">9%"),
                      "extension above 200MA (the live gate is 9%)")
        bucket_report(rows, lambda r: ("below MA20" if r["ma20d"] < 0
                                       else "0-2% above" if r["ma20d"] < 2 else ">2% above"),
                      "price vs 20-day MA (dip vs chase)")
        bucket_report(rows, lambda r: ("<50" if r["rsi"] < 50 else "50-60" if r["rsi"] < 60
                                       else "60-70" if r["rsi"] < 70 else ">70"),
                      "RSI(14)")
        bucket_report(rows, lambda r: ("fresh<=10d" if r["age"] <= 10
                                       else "11-30d" if r["age"] <= 30 else ">30d"),
                      "trend age (days since regime turned on)")
        bucket_report(rows, lambda r: ("0-1" if r["streak"] <= 1
                                       else "2-3" if r["streak"] <= 3 else "4+"),
                      "consecutive up closes")
        bucket_report(rows, lambda r: ("<13" if r["vix"] < 13 else "13-15" if r["vix"] < 15
                                       else "15-18"),
                      "VIX at entry")
        bucket_report(rows, lambda r: "breakout" if r["don"] else "inside range",
                      "Donchian-20 new-high day")
        bucket_report(rows, lambda r: ("3d pullback" if r["ret3"] < -0.5
                                       else "3d flat" if r["ret3"] < 1.0 else "3d rally"),
                      "prior 3-day move")
        bucket_report(rows, lambda r: r["dow"], "day of week")

        # ── OOS cross-check on the two headline candidates ──────────────
        # A bucket only counts if BOTH eras agree on its direction.
        bucket_report(rows, lambda r: f"{r['era']} ext<=9" if r["ext"] <= 9
                      else f"{r['era']} ext>9",
                      "OOS: extension gate x era", min_n=25)
        bucket_report(rows, lambda r: f"{r['era']} dip" if (r["ma20d"] < 0 or r["ret3"] < -0.5)
                      else f"{r['era']} no-dip",
                      "OOS: dip-entry (below MA20 or 3d pullback) x era", min_n=25)


if __name__ == "__main__":
    run()
