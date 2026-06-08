"""backtests/dipbuy_breadth_confirm_wf.py -- does requiring a BREADTH WASHOUT
improve the oversold dip-buy out-of-sample, or is it redundant with RSI<30?

The breadth study's STRONG finding was directional: washed-out breadth (<~p10 of
sectors above their 50d MA) -> SPY forward returns well above baseline, and the
deeper the washout the harder the bounce. That is the dip-buy thesis from an
independent lens. Unlike the condor (already VIX<18 gated, where breadth was
redundant — see condor_breadth_gate_wf), the dip-buy is NOT breadth-gated, so a
washout confirmer has room to add signal.

The honest risk: oversold (RSI<30) and low breadth both happen in selloffs, so
they are correlated by construction. The falsifiable question: among oversold
triggers, does ALSO demanding a breadth washout select BETTER bounces (deeper =
harder), or does it just shrink the sample without improving the edge?

We compare the parameter-free oversold dip-buy on:
  baseline           : every oversold trigger
  +washout (<=p10)   : triggers where 50d-breadth was washed out
  +low     (<=p25)   : triggers where 50d-breadth was merely low
priced with the same bull-debit engine, judged with the project's standard
expanding-window OOS folds + verdict gates. Research only.

Run: python -m backtests.dipbuy_breadth_confirm_wf
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def washout_confirm(triggers: pd.Series, breadth: pd.Series,
                    max_breadth: float) -> pd.Series:
    """AND the trigger mask with a breadth-washout mask (breadth <= max_breadth).
    Days with no breadth datum become False (don't confirm blind). Returns a
    boolean Series aligned to `triggers`' index."""
    b = breadth.reindex(triggers.index)
    confirmed = triggers.astype(bool) & (b <= max_breadth)
    return confirmed.fillna(False).astype(bool)


def _summary(trades):
    from backtests.dipbuy_wf import expanding_oos_folds, oos_metrics, wf_verdict
    folds = expanding_oos_folds(trades)
    m = oos_metrics(folds)
    return m, wf_verdict(m)


def main():
    from backtests.dipbuy_signal_study import load_spy, _triggers_for
    from backtests.dipbuy_option_wf import load_vix_at, price_dip_trades
    from backtests.sector_breadth_study import pct_above_ma, load_sector_panel

    spy = load_spy()
    vix_at = load_vix_at()
    trig = _triggers_for(spy, "oversold").reindex(spy.index, fill_value=False)

    panel = load_sector_panel()
    panel = {t: s.reindex(spy.index.union(s.index)).ffill().reindex(spy.index)
             for t, s in panel.items()}
    breadth = pct_above_ma(panel, 50)
    q = breadth.dropna()
    p10, p25 = round(float(q.quantile(0.10)), 1), round(float(q.quantile(0.25)), 1)

    variants = {
        "baseline (all oversold)": trig,
        f"+washout (<={p10:.0f}%)":  washout_confirm(trig, breadth, p10),
        f"+low      (<={p25:.0f}%)": washout_confirm(trig, breadth, p25),
    }

    print("Dip-buy + breadth-washout confirmer — expanding-window OOS")
    print(f"  50d-breadth p10(washout)={p10:.0f}%  p25(low)={p25:.0f}%  "
          f"| oversold triggers total: {int(trig.sum())}\n")
    print(f"  {'variant':<24} {'trig':>5} {'oosN':>5} {'mean$':>7} {'win':>5} {'sharpe':>7} {'pos-yr':>6}  verdict")
    base_m = None
    for name, mask in variants.items():
        trades = price_dip_trades(spy, vix_at, mask)
        m, v = _summary(trades)
        if base_m is None:
            base_m = m
        tag = "PASS" if v["passes"] else "fail"
        print(f"  {name:<24} {int(mask.sum()):>5} {m['n']:>5} ${m['mean_pnl']:>+6.0f} "
              f"{m['win_rate']:>4.0%} {m['sharpe']:>7.3f} {m['pos_year_frac']:>5.0%}  {tag}")

    print("\n  Read: does demanding a washout BEAT the baseline oversold dip-buy OOS")
    print("  (higher mean$/win/sharpe) AND keep enough triggers to be real? If it only")
    print("  shrinks n without lifting the edge, breadth is redundant with RSI<30.")


if __name__ == "__main__":
    main()
