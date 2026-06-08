"""backtests/dipbuy_horizon_sweep.py -- DTE horizon sweep of the oversold dip-buy.

Re-prices the validated oversold (RSI<30) bull-call debit at a range of expiries
to find the SHORTEST horizon at which the bounce edge survives. Answers: "can a
directional dip-buy work at 1-3DTE, or must it zoom out?" Research only.

Finding (2026-06-07, 34 triggers, in-sample): the edge needs ~5 days. 1-3DTE is
weak/coin-flippy (47-56% win); it switches on at ~5DTE (68%) and improves to 21
(76%). 5-7DTE is the capital-efficient sweet spot (~90% of the 21-day edge).

Run: python -m backtests.dipbuy_horizon_sweep
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.realistic_pricing import simulate_trade

DEFAULT_DTES = (1, 3, 5, 7, 10, 14, 21)


def sweep_horizons(spy_df, vix_at, triggers, *, dtes=DEFAULT_DTES,
                   profit_target: float = 0.50) -> dict:
    """For each DTE, price a bull-call debit on every trigger day (exit at
    `profit_target` of max profit OR hold-to-expiry) and aggregate. Returns
    {dte: {n, mean_pnl, win_rate, total_pnl, target_frac}}."""
    dates = list(spy_df.index)
    trig  = triggers.reindex(spy_df.index, fill_value=False)
    trig_idx = [i for i in range(len(dates)) if bool(trig.iloc[i])]
    out: dict[int, dict] = {}
    for dte in dtes:
        pnls, tgt = [], 0
        for i in trig_idx:
            r = simulate_trade(spy_df, dates, i, "bull_debit", vix_at,
                               entry_dte=dte, profit_target_pct=profit_target,
                               dte_close_threshold=0)   # hold to expiry unless target
            if r is None:
                continue
            pnls.append(r["pnl_dollars"])
            tgt += 1 if r.get("exit_reason") == "target" else 0
        n = len(pnls)
        out[dte] = {
            "n":           n,
            "mean_pnl":    round(sum(pnls) / n, 2) if n else 0.0,
            "win_rate":    round(sum(1 for p in pnls if p > 0) / n, 3) if n else 0.0,
            "total_pnl":   round(sum(pnls), 2),
            "target_frac": round(tgt / n, 3) if n else 0.0,
        }
    return out


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    from backtests.dipbuy_option_wf import load_vix_at
    spy = load_spy()
    trig = _triggers_for(spy, "oversold")
    res = sweep_horizons(spy, load_vix_at(), trig)
    print(f"Oversold dip-buy horizon sweep — {spy.index.min().date()}..{spy.index.max().date()}")
    print(f"{'DTE':>4}{'n':>5}{'mean':>9}{'win%':>7}{'total':>9}{'target%':>9}")
    for dte, m in res.items():
        print(f"{dte:>4}{m['n']:>5}{m['mean_pnl']:>9.2f}{m['win_rate']*100:>6.0f}%"
              f"{m['total_pnl']:>9.0f}{m['target_frac']*100:>8.0f}%")
    return res


if __name__ == "__main__":
    main()
