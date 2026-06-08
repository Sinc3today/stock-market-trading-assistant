# Secular-Regime Filter for the Dip-Buy (falsified — opposite direction)

**Date:** 2026-06-07 · **Module:** `backtests/secular_regime_filter.py` · Research only.

## Hypothesis
Buy-the-dip is a bull strategy; in a secular bear a dip is a falling knife. So gating the
dip-buy to "SPY above its 200d MA (secular bull)" should remove the losers.

## Result — FALSIFIED (the filter removes the BEST trades)

Full dip-buy book (RSI<30 ∪ 50d-low breakdown, 100 trades):

| Bucket | n | mean/trade | win% | total |
|---|---:|---:|---:|---:|
| Below 200d MA ("secular bear") | 59 | **+$125** | **70%** | +$7,361 |
| Above 200d MA ("secular bull") | 41 | +$41 | 58% | +$1,689 |

The dip-buy works **better** below the 200d MA. The deepest dips bounce hardest — the violent
mean-reversions (2020 COVID, 2022, 2018) all occurred below the MA and paid. Shallow above-MA
pullbacks give weaker bounces. A secular-bull gate would keep the worst and drop the best.

## Conclusion — do NOT add a secular-bull filter

It hurts in-sample, and it contradicts the core finding (deeper dips → bigger bounces, which
also validates the breakdown trigger). **Critical caveat:** 2010-2026 contains no 2008/2000-style
*relentless* secular bear (−50%, multi-year). Its "bears" (2022 −25%, 2020 −34% V-recovery) were
short and bounced. In a sustained bear, buy-the-dip *would* bleed — the **defined-risk debit
spread (bounded per-trade loss) is the only protection**, and that tail is genuinely untested by
this data. We accept it as a known, structurally-capped tail risk rather than a (counter-
productive) MA filter.

## Net
Reinforces: **SPY is a buy-the-dip instrument, and deeper dips are better.** Deploy the
breakdown trigger WITHOUT a secular gate.
