"""backtests/secular_regime_filter.py -- secular bull/bear filter for the dip-buy.

The long-timeframe protective factor we'd been under-weighting: buy-the-dip is a
bull-market strategy; in a secular BEAR a dip is a falling knife. Test: do the
dip-buy + breakdown LOSERS cluster when SPY is below its 200-day MA (secular
bear)? If so, an "only buy dips in a secular bull" gate removes them — the
causal falling-knife protection the HYG credit filter failed to provide.

Honest expectation: 2010-2026 is mostly secular-bull, so few trades fall in the
bear bucket — the filter is cheap *insurance* for a future bear more than an
in-sample booster. Research only. Run: python -m backtests.secular_regime_filter
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def secular_bull_flags(spy_df: pd.DataFrame, dates, ma_window: int = 200) -> dict:
    """date -> bool: True when SPY close is ABOVE its `ma_window`-day MA
    (secular bull). Defaults to True (bull) when the MA is undefined."""
    close = spy_df["close"].astype(float)
    ma = close.rolling(ma_window).mean()
    flags = {}
    for d in dates:
        d = pd.Timestamp(d)
        c = close.reindex(close.index.union([d])).ffill().get(d)
        m = ma.reindex(ma.index.union([d])).ffill().get(d)
        flags[d] = bool(c > m) if (pd.notna(c) and pd.notna(m)) else True
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


def split_by_secular(trades: list[dict], flags: dict) -> dict:
    """Split trades into secular bull vs bear by entry-date flag. 'bull' == the
    filtered book if we only buy dips in a secular bull."""
    bull = [t for t in trades if flags.get(pd.Timestamp(t["entry_date"]), True)]
    bear = [t for t in trades if not flags.get(pd.Timestamp(t["entry_date"]), True)]
    return {"bull": _stats(bull), "bear": _stats(bear),
            "bear_dates": [str(pd.Timestamp(t["entry_date"]).date()) for t in bear]}


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    from backtests.dipbuy_option_wf import load_vix_at, price_dip_trades, summarize
    from backtests.dipbuy_breakdown_study import breakdown_triggers
    spy = load_spy()
    vix_at = load_vix_at()
    # union of both buy-weakness triggers (the full dip-buy book)
    trig_rsi = _triggers_for(spy, "oversold")
    trig_bd  = breakdown_triggers(spy, window=50)
    trades = price_dip_trades(spy, vix_at, trig_rsi) + price_dip_trades(spy, vix_at, trig_bd)
    seen, union = set(), []
    for t in sorted(trades, key=lambda x: x["entry_date"]):
        if t["entry_date"] in seen:
            continue
        seen.add(t["entry_date"]); union.append(t)

    flags = secular_bull_flags(spy, [t["entry_date"] for t in union], ma_window=200)
    res = split_by_secular(union, flags)
    base = summarize(union)
    print(f"Secular-regime filter (SPY vs 200d MA) on the full dip-buy book "
          f"(RSI<30 ∪ 50d-low breakdown)\n")
    print(f"  unfiltered : n={base['n']}  mean=${base['mean_pnl']}  win={base['win_rate']:.0%}  total=${base['total_pnl']}")
    print(f"  secular BEAR (skip): n={res['bear']['n']}  mean=${res['bear']['mean_pnl']}  "
          f"win={res['bear']['win_rate']:.0%}  total=${res['bear']['total_pnl']}")
    print(f"  secular BULL (keep): n={res['bull']['n']}  mean=${res['bull']['mean_pnl']}  "
          f"win={res['bull']['win_rate']:.0%}  total=${res['bull']['total_pnl']}")
    print(f"\n  secular-bear (skipped) dates: {res['bear_dates']}")
    return res


if __name__ == "__main__":
    main()
