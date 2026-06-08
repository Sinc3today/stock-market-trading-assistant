# Transition-Zone Condor Sub-Condition Study (Study D, part 1)

**Date:** 2026-06-07 · **Module:** `backtests/condor_transition_study.py` · Research only.
**Data:** local 5yr daily backtest (2022-2026), synthetic-payoff model (indicative P&L).

## Question

The CHOPPY_TRANSITION condor (VIX 18-22) is a **−$2,950/5yr net loser** but **bimodal**
(good 2022/24/26 ~83% win, bad 2023/25 ~43%). Blanket-skip already **failed walk-forward**
(it would've hurt the good years). Is there a **causal sub-condition** that separates the
good days from the bad, so we can surgically fix it?

## Three grounded sub-conditions tested — none robustly works

| Sub-condition | Result | Verdict |
|---|---|---|
| **VIX direction** (rising = vol expanding) | rising: 62% win, −$250; **falling: 50% win, −$2,700**. And it flips by year (2023 damage is falling-VIX; 2025 damage is rising-VIX). "Skip rising" recovers ~$0. | ❌ falsified — losses don't cluster by vol direction |
| **Band position** (18-20 vs 20-22) | 18-20: −$560 (60% win); **20-22: −$2,390 (51% win)** — upper band worse, as expected near the elevated threshold. BUT the −$2,390 is mostly 2023 alone; still bimodal (2022/26 positive). | ⚠️ mild, not robust (single-year-driven) |
| **VIX term structure** (VIX9D>VIX3M backwardation) | only **7 backwardation days** in 5yr (113 contango) — insufficient sample. | ❌ inconclusive (too thin) |

## Conclusion

The transition-zone condor resists all three causally-grounded fixes. Combined with the
prior finding that blanket-skip fails walk-forward, the honest verdict is: **it's a
marginal, noisy, regime-dependent sub-strategy with no reliable fix.** The only mild signal
(upper band VIX 20-22 is worse) is largely a 2023 artifact and wouldn't survive OOS. **Stop
sinking effort into rescuing it** — leave it as-is (small, marginally-negative) or, if
anything, a *future* WF test of tightening the transition zone to 18-20 (weak evidence, not
recommended now).

## Where "expand the condor" should actually go

The transition zone is a dead end. The real condor edge is the **CALM condor**
(CHOPPY_LOW_VOL, VIX<18): **82% win, +$18,000** — already proven and traded. Expansion
effort is better spent either there (calm-regime sizing / entry timing — but it's already
well-tuned, diminishing returns) or, higher-value, on the **HYG risk-off filter** to protect
the live dip-buy, or simply letting the dip-buy forward-test accrue. Recorded so we don't
re-litigate the transition zone.
