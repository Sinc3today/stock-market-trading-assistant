"""backtests/dipbuy_hyg_filter.py -- HYG (credit) risk-off filter for the dip-buy.

The oversold dip-buy's only bad trades are falling-knife dips (2020 COVID). The
economic thesis: SPY oversold + credit OK = a buyable bounce; SPY oversold +
credit BLOWING OUT = a real risk-off cascade, don't catch it. HYG (high-yield
credit ETF) leads equities into risk-off. Risk-off proxy (one clean causal
definition): HYG below its 50-day MA (credit in a downtrend).

Study: split the oversold dip-buy trades by HYG state; does skipping risk-off
entries remove the losers while keeping the winners? Research first; only wire
into the live dip-buy if it clearly + consistently helps. Run:
    python -m backtests.dipbuy_hyg_filter
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

_HYG_CSV = os.path.join(os.path.dirname(__file__), "hyg_history.csv")


def load_hyg(path: str = _HYG_CSV) -> pd.Series:
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    col = "close" if "close" in df.columns else df.columns[-1]
    return df[col].astype(float)


def hyg_risk_off_flags(hyg: pd.Series, dates, ma_window: int = 50) -> dict:
    """date -> bool: True when HYG is BELOW its `ma_window`-day MA (credit
    downtrend = risk-off). Uses nearest-prior values (ffill) for alignment."""
    ma = hyg.rolling(ma_window).mean()
    flags = {}
    for d in dates:
        d = pd.Timestamp(d)
        h = hyg.reindex(hyg.index.union([d])).ffill().get(d)
        m = ma.reindex(ma.index.union([d])).ffill().get(d)
        flags[d] = bool(h < m) if (pd.notna(h) and pd.notna(m)) else False
    return flags


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    pnls = [t["pnl_dollars"] for t in trades]
    return {
        "n":         n,
        "total_pnl": round(sum(pnls), 2) if n else 0.0,
        "mean_pnl":  round(sum(pnls) / n, 2) if n else 0.0,
        "win_rate":  round(sum(1 for p in pnls if p > 0) / n, 3) if n else 0.0,
    }


def split_trades_by_hyg(trades: list[dict], flags: dict) -> dict:
    """Split trades into risk_off (HYG below MA at entry) vs ok. 'ok' == the
    filtered book if we skip risk-off entries."""
    ro = [t for t in trades if flags.get(pd.Timestamp(t["entry_date"]), False)]
    ok = [t for t in trades if not flags.get(pd.Timestamp(t["entry_date"]), False)]
    return {"risk_off": _stats(ro), "ok": _stats(ok),
            "risk_off_dates": [str(pd.Timestamp(t["entry_date"]).date()) for t in ro]}


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    from backtests.dipbuy_option_wf import load_vix_at, price_dip_trades, summarize
    spy = load_spy()
    trig = _triggers_for(spy, "oversold")
    trades = price_dip_trades(spy, load_vix_at(), trig)   # face IV, 21DTE bull debit
    flags = hyg_risk_off_flags(load_hyg(), [t["entry_date"] for t in trades])
    res = split_trades_by_hyg(trades, flags)
    base = summarize(trades)
    print("Oversold dip-buy — HYG risk-off filter (HYG < 50d MA = skip)\n")
    print(f"  unfiltered : n={base['n']}  mean=${base['mean_pnl']}  win={base['win_rate']:.0%}  total=${base['total_pnl']}")
    print(f"  risk-off   : n={res['risk_off']['n']}  mean=${res['risk_off']['mean_pnl']}  "
          f"win={res['risk_off']['win_rate']:.0%}  total=${res['risk_off']['total_pnl']}  (skipped)")
    print(f"  FILTERED   : n={res['ok']['n']}  mean=${res['ok']['mean_pnl']}  "
          f"win={res['ok']['win_rate']:.0%}  total=${res['ok']['total_pnl']}")
    print(f"\n  risk-off (skipped) entry dates: {res['risk_off_dates']}")
    return res


if __name__ == "__main__":
    main()
