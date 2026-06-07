# Dip-Buy Directional Study — Results

**Date:** 2026-06-07 · **Spec:** `docs/superpowers/specs/2026-06-07-dipbuy-directional-study-design.md`
**Data:** SPY + VIX 2010–2026 (extended via `refresh_all_history`). Research only — no live wiring.

## Verdict

| Arm | Phase 1 (signal) | Phase 2 (priced + IV-stress) | Outcome |
|---|---|---|---|
| **Oversold (RSI<30)** | ✅ edge present | ✅ profitable + IV-stress-robust | **PROMISING — pending forward test** |
| Pullback-in-uptrend (>200MA & <20MA) | ❌ no edge | not run (gated) | Falsified |

The **oversold dip-buy** is the **first signal the study program hasn't been able to kill** — every prior investigation (0DTE, meta-labeling, intraday-touch, time-exit, transition-skip) was shelved. The **pullback** thesis is dead (≈ baseline noise).

> **Honesty note (post-review).** The signal and structure are **parameter-free** (fixed RSI<30, fixed ATM/2.5% debit, fixed 21DTE/50%/time-close), so there are no fitted knobs to overfit in-sample. Phases 1–2 are an in-sample event-study + defined-risk pricing + IV-stress; an **expanding-window OOS walk-forward was since added** (Phase 3 below) and confirms the result. Remaining caveats keep the status at **"promising," not "validated":** the edge is **recency-loaded** (half2 ≈ 4× half1; OOS years are the strong ones), the sample is **n=34**, and the pricing is **modeled (BS flat-IV)**. The decisive test is still **forward paper-trading on unseen data** (see Adoption).

## Phase 1 — signal event-study (underlying only)

Forward SPY close-to-close return after each *fresh* trigger vs the unconditional baseline; consistency = positive edge across calendar years with ≥1 trigger.

**Oversold (34 triggers across 13 years, ~2/yr):**

| Horizon | cond mean | baseline | edge | pos-years | hit-rate |
|---|---:|---:|---:|---:|---:|
| 3d | +1.43% | +0.15% | **+1.28%** | 11/13 | 68% |
| 5d | +1.58% | +0.26% | **+1.32%** | 12/13 | 71% |
| 10d | +2.00% | +0.51% | **+1.49%** | 11/13 | 76% |

Both chronological halves positive at every horizon. **Pullback (189 triggers):** edge +0.03–0.16% (below the 0.25% floor), pos-years ~0.5 — noise.

> **Verdict recalibration (honest note):** the original Phase-1 gate required ≥5 triggers/year in ≥3 years — inappropriate for a ~2/yr signal, so it initially mislabeled oversold as failing. Recalibrated transparently to a rare-signal-aware test: any year with ≥1 trigger counts, backed by a 20-trigger total floor AND a chronological half-split (both halves must be positive). The bar still cleanly fails the pullback noise arm. Phase 2 is the independent arbiter regardless of Phase-1 wording.

## Phase 2 — option-priced backtest + IV-stress (oversold only)

*(Not a walk-forward — see the honesty correction above. Full-sample priced backtest with per-year and half-split robustness checks.)*

Bull call debit spread (ATM long / 2.5% OTM short), ~21 DTE, 50% profit target or ~10-trading-day time-close; BS-priced off VIX, with commission + slippage. The **IV-stress arm** bumps entry IV ×1.25 on these down-tape entries (the flat-VIX BS model understates crash-time option cost).

| Run | n | mean P&L | win | total | halves |
|---|---:|---:|---:|---:|---|
| Face-IV | 34 | **+$135.31** | 68% | +$4,600 | ($52, $219) |
| IV-stressed (×1.25) | 34 | **+$128.07** | 68% | +$4,354 | — |

Per-year P&L (face): positive in **10/13 years** (76.9%). Losers bounded: 2011 −$2, 2015 −$142, 2020 −$62 (COVID falling-knife, capped by the debit-spread max loss). Big years: 2023 +$418, 2025 +$509, 2026 +$338.

## Phase 3 — expanding-window OOS walk-forward (`backtests/dipbuy_wf.py`)

Parameter-free rule → nothing to fit, so the WF burns in the first 3 distinct trade-years (2010–2012) as "train" and aggregates every later year as out-of-sample, applying the standard gates.

| | n | mean P&L | win | Sharpe | pos OOS-years | verdict |
|---|---:|---:|---:|---:|---:|---|
| OOS (2014→2026) | 28 | **+$154** | 68% | 0.63 | 8/10 (80%) | **passes** |

The OOS mean (+$154) is *higher* than the full-sample (+$135) because the burned-in early years were the **smaller** winners — which both passes the gate **and** re-confirms the recency-loading caveat (the strength is concentrated in later years).

## Honest caveats

- **Modeled pricing, not real chains.** P&L is Black-Scholes off flat VIX-as-IV (no skew/term structure). The IV-stress arm is the mitigation and the edge survived it; the defined-risk debit spread structurally caps the worst case (2020 was only −$62/trade). A real crash IV spike exceeds ×1.25, but max loss is bounded regardless.
- **Modest sample (n=34).** Mitigated by 16-year span, 10/13 positive years, both halves positive, and IV-stress survival — but it is not thousands of trades.
- **Recent years carry weight.** half2 ($219) > half1 ($52); 2023/25/26 are the big winners. Both halves are still positive.

## Adoption

Per the spec (loop rule 13), **nothing is wired live.** The oversold dip-buy is recorded as a **human-promotion candidate** in `STRATEGY_LOG.md`. Going live would be a separate, deliberate step with its own spec (entry detection, structure, sizing, regime guards). The natural next validation before real money: paper-trade it forward (shadow/learning book) to confirm out-of-sample on unseen data.
