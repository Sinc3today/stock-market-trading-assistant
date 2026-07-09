"""backtests/dipbuy_multi_instrument.py -- does the dip-buy travel to QQQ / IWM?

The oversold dip-buy (RSI<30 -> ATM/2.5% bull debit, 21DTE/50%/~10td exit) is
WF-validated on SPY (+$154/trade, 68%). It's a DEBIT — no collateral — so if the
same parameter-free rule works on QQQ/IWM it adds trade frequency without
touching buying power (user constraint, 2026-07-09).

Method: the EXACT SPY pipeline (same signal fns, same option pricer, same
expanding-window OOS folds + verdict gates) run per instrument on yfinance
history from 2010. Honest caveats printed with the results:
  - sigma: VIX is SPY's vol; for QQQ/IWM it's an approximation (QQQ realized vol
    runs ~1.15-1.3x SPY). Debits are therefore UNDERSTATED for QQQ/IWM — wins
    are cheaper than reality. Directional edge (hit rate, per-year consistency)
    is the trustworthy part; absolute P&L less so.
  - parameter-free rule -> nothing is fit per instrument; this is pure OOS
    transfer of a fixed rule.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from backtests.dipbuy_signal_study import rsi_series, oversold_triggers
from backtests.dipbuy_option_wf import load_vix_at, price_dip_trades
from backtests.dipbuy_wf import (
    expanding_oos_folds, oos_metrics,
    WF_MIN_OOS_WIN_RATE, WF_MIN_OOS_PNL, WF_MIN_OOS_SHARPE, WF_MIN_OOS_YEAR_FRAC,
)


def load_instrument(ticker: str, start: str = "2010-01-01") -> pd.DataFrame:
    import yfinance as yf
    h = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    h.index = pd.to_datetime(h.index).tz_localize(None)
    df = h[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = [d.date() for d in df.index]
    return df.sort_index()


def run_instrument(ticker: str, vix_at: dict) -> dict:
    df = load_instrument(ticker)
    rsi = rsi_series(df["close"])
    trig = oversold_triggers(rsi, threshold=30.0)
    trades = price_dip_trades(df, vix_at, trig)
    for t in trades:
        t["entry_year"] = pd.Timestamp(t["entry_date"]).year \
            if not hasattr(t["entry_date"], "year") else t["entry_date"].year
    folds = expanding_oos_folds(trades)
    m = oos_metrics(folds)
    m["ticker"] = ticker
    m["total_trades"] = len(trades)
    m["verdict"] = ("PASS" if (m["n"] >= 20
                               and m["win_rate"] >= WF_MIN_OOS_WIN_RATE
                               and m["mean_pnl"] > WF_MIN_OOS_PNL
                               and m["sharpe"] > WF_MIN_OOS_SHARPE
                               and m["pos_year_frac"] >= WF_MIN_OOS_YEAR_FRAC)
                    else "FAIL")
    return m


def main():
    vix_at = load_vix_at()
    print(f"{'ticker':>7}{'trades':>8}{'OOS n':>7}{'win%':>8}{'avgP&L':>9}"
          f"{'sharpe':>8}{'posYr%':>8}{'verdict':>9}")
    for ticker in ("SPY", "QQQ", "IWM"):
        try:
            m = run_instrument(ticker, vix_at)
            print(f"{ticker:>7}{m['total_trades']:>8}{m['n']:>7}"
                  f"{m['win_rate']*100:>7.1f}%{m['mean_pnl']:>9.2f}"
                  f"{m['sharpe']:>8.3f}{m['pos_year_frac']*100:>7.0f}%"
                  f"{m['verdict']:>9}")
            print(f"        per-year OOS mean: { {y: v for y, v in sorted(m['per_year'].items())} }")
        except Exception as e:
            print(f"{ticker:>7}  ERROR: {e}")
    print("\ncaveat: sigma=VIX for all instruments (SPY's vol) — QQQ/IWM debits "
          "understated; trust hit-rate/consistency over absolute P&L.")


if __name__ == "__main__":
    main()
