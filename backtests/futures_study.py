"""backtests/futures_study.py -- what do ES futures actually give us?

User question (2026-07-11): do futures help with predictions / how do
"instantaneous" futures moves play out down the line?

Two testable claims:
  A. SENTINEL VALUE (information timing): how much of the Fri-close->Mon-open
     SPY gap is already visible in ES futures by Sunday ~10 PM ET? If most of
     it, a Sunday-evening check gives ~11h of warning on weekend gap risk
     (docs/GAP_CONDITIONAL_STUDY.md) while positions sit exposed.
  B. PREDICTIVE VALUE: does the overnight ES move predict the NEXT SPY session
     (open->close)? Fair-value arbitrage guarantees futures predict the OPEN —
     that's mechanics, not edge. The session after is the real question.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def load_daily(ticker, start="2018-01-01"):
    import yfinance as yf
    h = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    h.index = pd.to_datetime(h.index).tz_localize(None)
    return h


def part_a_sunday_sentinel():
    import yfinance as yf
    es = yf.Ticker("ES=F").history(period="730d", interval="1h")
    es.index = pd.to_datetime(es.index).tz_convert("US/Eastern")
    spy = load_daily("SPY", start="2024-01-01")
    spy_by_date = {d.date(): (float(r["Open"]), float(r["Close"]))
                   for d, r in spy.iterrows()}
    rows = []
    dates = sorted({ts.date() for ts in es.index})
    for d in dates:
        if pd.Timestamp(d).weekday() != 6:            # Sundays
            continue
        fri = d - pd.Timedelta(days=2).to_pytimedelta()
        mon = d + pd.Timedelta(days=1).to_pytimedelta()
        fri_bars = es[es.index.date == fri]
        sun_bars = es[(es.index.date == d) & (es.index.hour <= 22)]
        if fri_bars.empty or sun_bars.empty or fri not in spy_by_date or mon not in spy_by_date:
            continue
        es_fri_close = float(fri_bars["Close"].iloc[-1])
        es_sun_22 = float(sun_bars["Close"].iloc[-1])
        known = (es_sun_22 - es_fri_close) / es_fri_close * 100
        spy_fri_close = spy_by_date[fri][1]
        spy_mon_open = spy_by_date[mon][0]
        final = (spy_mon_open - spy_fri_close) / spy_fri_close * 100
        rows.append({"known": known, "final": final})
    r = pd.DataFrame(rows)
    corr = r["known"].corr(r["final"])
    same_sign = ((r["known"] * r["final"]) > 0) | (r["final"].abs() < 0.05)
    big = r[r["final"].abs() > 0.5]
    caught = ((big["known"] * big["final"] > 0) & (big["known"].abs() > 0.2)).mean() if len(big) else float("nan")
    print(f"== A. Sunday-evening sentinel ({len(r)} weekends, 2024+) ==")
    print(f"  corr(Sun-10pm ES move, Mon SPY open gap): {corr:.2f}")
    print(f"  direction already right by Sun 10pm:      {same_sign.mean()*100:.0f}%")
    print(f"  of Monday gaps >0.5%: visible (>0.2% same-dir) by Sun 10pm in "
          f"{caught*100:.0f}% (n={len(big)})")


def part_b_overnight_prediction():
    es = load_daily("ES=F")
    spy = load_daily("SPY")
    df = pd.DataFrame({
        "es_close": es["Close"], "spy_open": spy["Open"], "spy_close": spy["Close"],
    }).dropna()
    df["overnight"] = (df["spy_open"] - df["spy_close"].shift(1)) / df["spy_close"].shift(1) * 100
    df["session"] = (df["spy_close"] - df["spy_open"]) / df["spy_open"] * 100
    df = df.dropna()
    corr = df["overnight"].corr(df["session"])
    print(f"\n== B. does the overnight move predict the SESSION? ({len(df)} days, 2018+) ==")
    print(f"  corr(overnight gap, following open->close session): {corr:+.3f}")
    for lo, hi, label in ((-99, -1, "gap < -1%"), (-1, -0.3, "gap -1..-0.3%"),
                          (-0.3, 0.3, "gap flat"), (0.3, 1, "gap +0.3..1%"),
                          (1, 99, "gap > +1%")):
        s = df[(df["overnight"] > lo) & (df["overnight"] <= hi)]["session"]
        if len(s) < 20:
            continue
        print(f"  {label:>14}: n={len(s):<4} session mean {s.mean():+.3f}%  "
              f"pos {((s > 0).mean()*100):.0f}%")


if __name__ == "__main__":
    part_a_sunday_sentinel()
    part_b_overnight_prediction()
