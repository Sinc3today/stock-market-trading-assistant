"""backtests/qqq_condor_transfer.py -- does the calm-regime condor transfer to QQQ?

Deploy-realistic design: the bot classifies THE MARKET (SPY + VIX — unchanged),
and on condor days trades QQQ's chain instead of / alongside SPY's. So:
  - entry days  = the SAME CHOPPY_LOW_VOL/TRANSITION days the live gate picks
  - structure   = same 2.5% shorts / 2% wings / 70% target / 21-DTE close
  - pricing     = QQQ spot with ^VXN sigma (the Nasdaq vol index — QQQ options
                  really are priced off higher vol than SPY's; using VIX would
                  overstate the edge)
Baseline printed alongside: SPY condors on the same days (the known result).
Pure OOS transfer of a fixed rule — nothing fit to QQQ.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.structure_comparison import condor_legs, simulate, _classify_entries


def load_yf(ticker: str, start: str):
    import yfinance as yf
    h = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    h.index = pd.to_datetime(h.index).tz_localize(None)
    df = h[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = [d.date() for d in df.index]
    return df.sort_index()


def summarize(name, rows):
    n = len(rows)
    if not n:
        print(f"{name:>14}: no trades")
        return
    wins = sum(1 for r in rows if r["outcome"] == "win")
    tot = sum(r["pnl"] for r in rows)
    cap = sum(r["capital"] for r in rows) / n
    print(f"{name:>14}: n={n:<4} win {wins/n*100:5.1f}%  total ${tot:>9,.0f}  "
          f"avg ${tot/n:>7.2f}  cap ${cap:>5.0f}  ROC {(tot/n)/cap*100:5.1f}%")
    by_year = {}
    for r in rows:
        by_year.setdefault(r["entry_date"].year, []).append(r["pnl"])
    yrs = {y: round(sum(v) / len(v), 0) for y, v in sorted(by_year.items())}
    print(f"{'':>14}  per-year avg: {yrs}")


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()} \
        if vix_df is not None and len(vix_df) else {}
    dates, entries = _classify_entries(spy_df, vix_at)
    entry_dates = [dates[i] for i in entries]
    print(f"SPY-regime condor days: {len(entry_dates)}")

    start = str(spy_df.index[0])
    qqq = load_yf("QQQ", start)
    vxn_df = load_yf("^VXN", start)
    vxn_at = {d: float(c) for d, c in vxn_df["close"].items()}
    qdates = sorted(qqq.index)
    qidx = {d: i for i, d in enumerate(qdates)}

    spy_rows, qqq_rows = [], []
    for k, d in enumerate(entry_dates):
        # SPY baseline (VIX sigma — the known engine)
        i = entries[k]
        spot = float(spy_df.loc[d, "close"])
        r = simulate(condor_legs(spot, 0.020), spy_df, dates, i, vix_at)
        if r:
            r["entry_date"] = d
            spy_rows.append(r)
        # QQQ transfer (VXN sigma)
        j = qidx.get(d)
        if j is None or j > len(qdates) - 35:
            continue
        qspot = float(qqq.loc[d, "close"])
        rq = simulate(condor_legs(qspot, 0.020), qqq, qdates, j, vxn_at)
        if rq:
            rq["entry_date"] = d
            qqq_rows.append(rq)

    summarize("SPY (baseline)", spy_rows)
    summarize("QQQ (VXN)", qqq_rows)
    print("\nnote: same entry days (SPY/VIX regime — as the bot would deploy);"
          "\nQQQ priced with ^VXN so credits/exits reflect Nasdaq vol, not SPY's.")


if __name__ == "__main__":
    run()
