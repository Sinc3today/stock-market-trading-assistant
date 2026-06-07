# Regime Playbook & Scorecard

**Generated:** 2026-06-06 · **Source:** `backtests/spy_daily_backtest.py --source local --years 5` (2022-04-05 → 2026-05-15, 1,032 days)

> **Honesty caveat — read first.** The daily backtest prices directional debit
> spreads with a **synthetic fixed-payoff model** (flat win/loss dollars keyed off
> SPY % move), *not* real or Black-Scholes option marks. So P&L below is directionally
> indicative, not ground truth — especially for the directional (bull/bear) plays.
> The iron-condor numbers are the most trustworthy because the condor edge has also
> been validated elsewhere; the directional numbers should be treated as a weak prior
> pending the real-priced walk-forward study (thread ②).

## Scorecard — the 7 regimes (post-2026-06-06 label split + bear guards)

| Regime | Trigger | Action | Days | Win% | P&L (1-ct) | Edge basis |
|---|---|---|---:|---:|---:|---|
| **Choppy Low Vol** (calm) | not trending (ADX<32) & VIX<18 | **iron condor** | 265 | **82.3%** | **+$18,000** | ✅ **Validated, and STRONGER than thought** — once the transition zone is split out, the true calm edge is 82.3% / +$18,000 (was masked at 74% / +$15,050 by the blended bucket). |
| **Choppy Transition** | not trending & VIX 18–22 | half-size condor | 120 | 55.8% | **−$2,950** | ⚠️ **BIMODAL, not a clean loser.** Net-negative but +ve in 3/5 yrs (2022/24/26 ~83% win) and badly −ve in 2 (2023 −$3,650, 2025 −$2,010). Blanket-skip **fails walk-forward** (helps ~half the OOS windows, hurts the other half). Needs a *sub-condition* fix, not a toggle. |
| Trending Up Calm | ADX≥32, >200MA, VIX<22 | bull debit (52% skip) | 366 | 51.1% | +$6,720 | ⚠️ **Weak/soft.** ~Coin-flip win rate; rests on the synthetic payoff model. Guardrails are backtested; the play itself isn't real-priced. |
| Trending Down Calm | ADX≥32, <200MA, VIX<22 | bear debit | 15 | 40.0% | **−$390** | ❌ **No edge.** With the new symmetric guardrails the sample is 15 trades (extremes now skipped); still negative. The "downtrend/dip" play has no validated edge. |
| Choppy High Vol | not trending & VIX≥22 | **skip** | 125 | — | $0 | ✅ Skip validated ("condor poison" in vol expansion). |
| Trending High Vol | ADX≥32 & VIX≥22 | **skip** | 115 | — | $0 | ✅ Skip validated by a *prior* trade-it experiment (19% win, −$4,600 / 5yr). $0 just means it's currently skipped. |
| Unknown | <200 bars, or trend <1.5% from MA (either side) | **skip** | 26 | — | $0 | Safety fallback (now includes too-close-to-MA *down*-trends too). |

> **Headline finding from the split:** the transition-zone condor (VIX 18–22) lost **−$2,950 over 5 years** while hiding inside the calm-condor bucket. Removing it reveals the true calm-condor edge is **82.3% / +$18,000** — materially better than the previously-reported blend.
>
> **Walk-forward follow-up (2026-06-07):** blanket-skipping the transition zone **does NOT survive walk-forward.** The loss is bimodal — positive in 3/5 years (2022/24/26, ~83% win) and concentrated in 2 bad years (2023 −$3,650, 2025 −$2,010). Because transition days are independent, the OOS benefit of skipping each year is exactly minus that year's transition P&L, which flips sign across windows (helps 2023/25, hurts 2022/24/26). So the "easy win" is a mirage of the net number. The real, harder question: **what sub-condition separates the ~83%-win transition years from the ~43% ones?** (rising-vs-falling VIX, position within the 18–22 band, etc.) — folded into thread ②.

## By-year reality check

| Year | Trades | Win% | P&L | Sharpe |
|---|---:|---:|---:|---:|
| 2022 (bear) | 31 | 67.7% | +$1,060 | 3.48 |
| **2023** | 178 | 50.6% | **−$600** | **−0.34** |
| 2024 | 134 | 79.9% | +$10,160 | 9.83 |
| 2025 | 183 | 66.7% | +$7,110 | 4.26 |
| 2026 (YTD) | 62 | 79.0% | +$4,170 | 7.85 |

**2023 was a losing year** (−$600, negative Sharpe) — the strategy is not monotonically profitable, and a benign-regime stretch (2024-2026) flatters the headline. Worth remembering before sizing up.

## What this says

1. **The iron condor in *calm* markets is the only robust edge — and it's stronger than we thought.** Splitting out the transition zone reveals 82.3% win, +$18,000 (the real core driver). Everything else is marginal, unproven, or negative.
2. **The transition-zone condor (VIX 18–22) is a net loser** (−$2,950 / 5yr) that was inflating-then-deflating the headline. Highest-value follow-up: skip it (via WF/hypothesis check).
3. **Directional plays are weak-to-absent under the model.** Bull-debit ~51% coin-flip; bear-debit is now −$390 on 15 trades after the symmetric guards. The bot has **no validated dip/downtrend edge today** — confirming why thread ② (real-priced directional walk-forward) is the keystone.
4. **The skips are doing their job.** High-vol and unknown regimes correctly produce $0, not losses.

## Defects — STATUS: all fixed 2026-06-06 (branch `regime-defects`)

- ✅ **Unvalidated half-size condor (VIX 18–22):** split into its own `CHOPPY_TRANSITION` label (behavior preserved). Immediately exposed it as a −$2,950 loser; scorecard above updated.
- ✅ **Bear-side guardrail asymmetry:** the 1.5% separation floor and 9% extension cap now mirror onto the trending-down branch (using `abs(ma_dist_pct)`).
- ✅ **Dead flag:** `config.REGIME_FILTER_ENABLED` removed (was read nowhere).
- ✅ **Missing doc:** `STRATEGY_LOG.md` created.

## Implications for the next threads

- **② Directional backtest (keystone):** the bar to beat is low and already known — bull-debit ~51%, bear-debit ~nil under the synthetic model. The real question is whether a *properly-defined, real-priced* dip-buy / trend-follow beats that baseline **out-of-sample**, with an IV-stress arm (flat-VIX BS understates crash-time option cost) and awareness that 2022 is the only major bear in-sample.
- **① Shadow extension:** only the trending-down (directional) skip is cheaply shadowable today; dip/high-vol regimes have no defined trade to counterfactually price until ② defines one.
