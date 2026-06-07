# Dip-Buy Directional Study — Design

**Date:** 2026-06-07
**Status:** Design approved (Parts 1 & 2). Ready for spec review → implementation plan.
**Branch (anticipated):** `dipbuy-study`

## Problem & Goal

The regime scorecard (`docs/REGIME_PLAYBOOK.md`) confirmed the bot has **no validated
dip/downtrend edge**: the calm-market iron condor is the only robust edge; directional
plays are a ~51% coin-flip (bull) or statistical noise (bear). The user's question —
"is now a time to buy the dip?" — has no evidence-based answer today.

**Goal:** determine, with the project's falsification-first walk-forward discipline,
whether a **dip-buy** strategy has a real out-of-sample edge — and do it the cheapest
honest way (prove the underlying signal exists *before* building option-pricing
machinery). This is a **research study**, not a live-trading change. See
[[feedback_walk_forward_honest_ml]], [[feedback_falsification_anti_bias]].

## Scope

- **Dip-buy only** (not trend-follow, not the existing weak bull/bear plays).
- **Two arms**, one clean definition each (minimal knobs):
  1. **Oversold bounce** — `RSI(14) < 30`. Pure mean-reversion; fires in any regime.
  2. **Pullback-in-uptrend** — `SPY close > 200-day MA` AND `SPY close < 20-day MA`.
     Trend-aligned; structurally avoids bear markets.
- **History 2010–2026** (extend the local data), so 2018/2020/2022 drawdowns + ~15y of
  corrections are in-sample.
- **No live wiring.** A winning arm becomes a documented promotion candidate only
  (loop rule 13: the runner never edits source).

## Architecture — two phases, Phase 2 gated by Phase 1

```
Phase 0  Data prep: extend spy_history.csv + VIX history to 2010 (one-time)
Phase 1  Signal event-study (underlying only, NO options)
            → forward returns after each trigger, OOS, vs baseline
            → per-arm verdict: survives or not
Phase 2  (ONLY for surviving arms) option-priced walk-forward
            → bull call debit spread, face-IV + IV-stressed, WF verdict
Output   docs/DIPBUY_STUDY.md + KB entry (confirming / disconfirming)
```

The phase gate is the core anti-waste decision: if SPY does not bounce after a trigger
out-of-sample, no option structure rescues it, so Phase 2 never builds for that arm.

## Phase 0 — Data prep

- Extend `backtests/spy_history.csv` to start 2010-01-01 using the existing
  download/refresh tooling (`backtests/download_spy.py` / `refresh_all_history.py`,
  yfinance). Verify date span and bar count after.
- Ensure VIX daily history covers 2010+ for the same span (CBOE CSV via `vix_client`;
  the CBOE series predates 2010). VIX is needed by Phase 2 (IV proxy), not Phase 1.
- This phase only extends data; it does not touch the live data path.

## Phase 1 — Signal event-study (underlying only)

**Module:** `backtests/dipbuy_signal_study.py` (new, pure/offline, no network at run time
beyond reading the CSV).

- **Triggers** computed from daily bars using existing indicators where possible
  (`indicators/rsi.py` for RSI(14); `indicators/moving_averages.py` for MA20/MA200).
- For each day `T` where an arm's trigger fires, record SPY **close-to-close forward
  return** at **T+3, T+5, T+10** trading days.
- **Baseline:** the same forward-return distribution over *all* trading days (the
  unconditional prior).
- **Aggregates per arm × horizon:** n, mean fwd return, median, **% positive**, and the
  **edge = conditional mean − baseline mean**.
- **Per-year consistency check (the "walk-forward" for Phase 1):** the triggers have NO
  fitted parameters (fixed RSI/MA thresholds), so there is no in-sample/out-of-sample
  *fitting* to overfit. The honest robustness test is therefore temporal consistency:
  compute the arm's edge **per calendar year** (2010…2026) vs that year's baseline, and
  report the **fraction of years with positive edge**. This is what caught the
  transition-zone trap — a positive net driven by a few years isn't an edge.

**Phase 1 verdict (gate to Phase 2)** — an arm SURVIVES only if ALL hold:
1. Pooled OOS conditional mean forward return is **positive** at ≥1 horizon.
2. Edge vs baseline ≥ a meaningful margin (config: `DIPBUY_MIN_EDGE_PCT`, default
   **0.25%** at the chosen horizon).
3. **Consistency:** positive edge in **≥60%** of calendar years (`DIPBUY_MIN_OOS_YEAR_FRAC`),
   counting only years with ≥ `DIPBUY_MIN_TRIGGERS_PER_WINDOW` triggers.
Otherwise: NOT survived → documented, Phase 2 skipped for that arm. (This is the same
"don't be fooled by a net number" guard the transition-zone check applied.)

Thresholds live in `config.py` so they're explicit and adjustable.

## Phase 2 — Option-priced walk-forward (conditional)

Builds **only** for arms that survived Phase 1.

**Module:** `backtests/dipbuy_option_wf.py` (new), reusing existing infra:
- **Structure:** `realistic_pricing.build_legs("bull_debit", spot, …)` — ATM long /
  ~2.5% OTM short bull call debit spread.
- **Entry:** ~**21 DTE** on the trigger day.
- **Exit:** `simulate_trade(...)` with **profit target 50%** OR **time-close after 10
  trading days**, whichever first (the bounce window from Phase 1).
- **Pricing:** Black-Scholes with VIX/100 as IV (the model's known flat-IV limitation).
- **IV-stress arm (credibility guard):** every arm runs **twice** — face IV and entry IV
  **stressed up** by `DIPBUY_IV_STRESS_MULT` (default **1.25**) on down-tape entries.
  An edge must survive **both** to be trusted; one that evaporates under stress is
  flagged a pricing artifact.
- **Walk-forward + verdict:** expanding-window via the existing `walk_forward.py` machinery
  and its OOS-retention verdict (edge GENERALISES if OOS ≥ 70% of in-sample Sharpe),
  plus the intraday-WF verdict gates (`MIN_OOS_WIN_RATE`, `MIN_OOS_PNL`, `MIN_OOS_SHARPE`,
  `MIN_DELTA_PNL_PER_TRADE`). Per arm × (face-IV, IV-stressed) × window pass/fail.

## Adoption

No automatic wiring. A surviving + IV-stress-robust + WF-passing arm is recorded in
`STRATEGY_LOG.md` as a **human-promotion candidate**. Live dip-buy is a separate,
deliberate step taken only if the user chooses. If nothing passes, the study ships as a
documented negative result — a valid, valuable outcome.

## Error Handling

- Missing/short bars or a pricing failure → drop that trade, log it (no silent truncation).
- A window/year with too few triggers (`< DIPBUY_MIN_TRIGGERS_PER_WINDOW`, default 5)
  reports `inconclusive`, never a forced pass/fail.
- Data-extension failures (Phase 0) abort with a clear error rather than running on a
  partial CSV.

## Testing (TDD)

- **Phase 1:** forward-return computation on a synthetic series with known T+N values;
  RSI(14)<30 trigger fires exactly on a constructed oversold series; pullback trigger
  fires only when above-200 & below-20 both hold; conditional-vs-baseline edge math;
  the survives/not-survives verdict on a planted-positive-edge vs planted-noise series;
  the ≥60%-OOS-year consistency rule.
- **Phase 2:** debit-spread P&L on a known BS fixture; the IV-stress arm yields a higher
  entry cost (and ≤ face-IV P&L); the WF verdict classifies a planted-edge series as
  GENERALISES and a planted-noise series as OVERFIT/inconclusive.
- Offline only; deselect the flaky live-FRED tests as usual.

## Out of Scope (explicit)

- Trend-follow, re-enabling `TRENDING_HIGH_VOL`, the transition-zone sub-condition (those
  are separate threads).
- Any change to live trading, scanners, or the scheduler.
- Real intraday option chains (Phase 2 uses the modeled BS price; the IV-stress arm is the
  honesty mitigation, not a replacement for real chains).

## Follow-ups (noted, not in this spec)

- If a dip arm passes: a live dip-buy wiring spec (separate).
- The transition-zone sub-condition investigation (separate thread).
- Trend-follow as a sibling study reusing this harness.
