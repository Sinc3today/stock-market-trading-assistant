# Regime Playbook & Scorecard

**Generated:** 2026-06-06 · **Source:** `backtests/spy_daily_backtest.py --source local --years 5` (2022-04-05 → 2026-05-15, 1,032 days)

> **Honesty caveat — read first.** The daily backtest prices directional debit
> spreads with a **synthetic fixed-payoff model** (flat win/loss dollars keyed off
> SPY % move), *not* real or Black-Scholes option marks. So P&L below is directionally
> indicative, not ground truth — especially for the directional (bull/bear) plays.
> The iron-condor numbers are the most trustworthy because the condor edge has also
> been validated elsewhere; the directional numbers should be treated as a weak prior
> pending the real-priced walk-forward study (thread ②).

## Scorecard — the 6 regimes

| Regime | Trigger | Action | Days | Win% | P&L (1-ct) | Edge basis |
|---|---|---|---:|---:|---:|---|
| **Choppy Low Vol** (calm) | not trending (ADX<32) & VIX<18 | **iron condor** | 385 | **74.0%** | **+$15,050** | ✅ **Validated** — the core edge; 69% of all P&L. Corroborated by the 5yr 74.1% condor win rate. |
| Trending Up Calm | ADX≥32, >200MA, VIX<22 | bull debit (52% skip) | 366 | 51.1% | +$6,720 | ⚠️ **Weak/soft.** ~Coin-flip win rate; rests on the synthetic payoff model. Guardrails (1.5% sep floor, 9% extension cap) are backtested; the play itself isn't real-priced. |
| Trending Down Calm | ADX≥32, <200MA, VIX<22 | bear debit | 29 | 51.7% | **+$130** | ❌ **Not validated.** 29 trades in 5yr (~$4.5/trade) = statistically meaningless. The "downtrend/dip" play shows **no meaningful edge**. Also missing the guardrails its up-trend mirror has. |
| Choppy High Vol | not trending & VIX≥22 | **skip** | 125 | — | $0 | ✅ Skip validated ("condor poison" in vol expansion). |
| Trending High Vol | ADX≥32 & VIX≥22 | **skip** | 115 | — | $0 | ✅ Skip validated by a *prior* trade-it experiment (19% win, −$4,600 / 5yr). The $0 here just means it's currently skipped. |
| Unknown | <200 bars, or trending-up but <1.5% from MA | **skip** | 12 | — | $0 | Safety fallback. |

*(A 7th hidden branch: the **half-size "transition-zone" condor** at VIX 18–22 is emitted under the `Choppy Low Vol` label with confidence hardcoded to 0.50 — see Defects. Its stats are buried inside the condor bucket and can't be read separately.)*

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

1. **The iron condor in calm markets is the only robust edge.** 74% win, +$15,050 (69% of total), consistent across years. Everything else is marginal or unproven.
2. **Directional plays are weak-to-absent under the model.** Bull-debit is a ~51% coin-flip; bear-debit is statistically nothing (29 trades, +$130). The bot has **no validated dip/downtrend edge today** — confirming why thread ② (real-priced directional walk-forward) is the keystone, not a nice-to-have.
3. **The skips are doing their job.** High-vol and unknown regimes correctly produce $0, not losses.

## Defects surfaced (worth fixing regardless)

- **Unvalidated half-size condor (VIX 18–22):** `tradeable=True`, confidence hardcoded 0.50, "or sit out" hedge in its own reasons, no backtest. Hidden inside the `Choppy Low Vol` bucket so it's invisible to the per-regime report. → *Split it into its own regime label so it's measurable.*
- **Bear-side guardrail asymmetry:** `Trending Down Calm` skips the 1.5% separation floor and 9% extension cap that `Trending Up Calm` has (those live only in the `above_ma` branch). The less-validated side is *less* protected.
- **Dead flag:** `config.REGIME_FILTER_ENABLED` is defined but read **nowhere** — the detector always runs. Remove or wire it.
- **Missing doc:** `CLAUDE.md` references `STRATEGY_LOG.md`, which does not exist in the repo.

## Implications for the next threads

- **② Directional backtest (keystone):** the bar to beat is low and already known — bull-debit ~51%, bear-debit ~nil under the synthetic model. The real question is whether a *properly-defined, real-priced* dip-buy / trend-follow beats that baseline **out-of-sample**, with an IV-stress arm (flat-VIX BS understates crash-time option cost) and awareness that 2022 is the only major bear in-sample.
- **① Shadow extension:** only the trending-down (directional) skip is cheaply shadowable today; dip/high-vol regimes have no defined trade to counterfactually price until ② defines one.
