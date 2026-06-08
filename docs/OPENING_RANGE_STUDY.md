# Opening-Range 0DTE Signal Study — Results (Phase 1)

**Date:** 2026-06-07 · **Module:** `backtests/opening_range_study.py` · Research only.
**Data:** SPY 5-min bars, 2024-06 → 2026-06 (500 sessions, Polygon Starter ~2yr limit, cached to parquet).

## Question

Does the **15-min opening-range breakout** (or the overnight **gap**) predict the **rest-of-day** SPY direction, out-of-sample? This is the exact entry signal the bot's existing (losing) 0DTE directional plays already use (`intraday_backtest`'s OR+VWAP blend) — so Phase 1 isolates whether the **signal** is the problem or the **structure/exit**. Underlying-only; gates an optional Phase 2 (0DTE option pricing).

## Result (baseline rest-of-day drift +0.023%)

| Arm | n | cond. rest-of-day | per-year edge | read |
|---|---:|---:|---|---|
| **break_up** (close > 15-min OR high) | 90 | **+0.110%** (63% pos) | 2024 −0.02 / 2025 +0.12 / 2026 +0.09 | small momentum, mostly-positive (2/3 yrs) |
| **break_down** (close < OR low) | 73 | +0.108% (*reverts up*) | 2024 +0.45 / 2025 +0.04 / **2026 −0.22** | **not robust — flips sign by year** |
| **gap_down** | 202 | +0.113% (*reverts up*) | +0.01 / +0.15 / +0.05 | small, consistently positive (fade up) |
| gap_up | 296 | −0.039% | — | no edge |

## Conclusion — Phase 2 NOT warranted; 0DTE directional is closed

1. **Edges are real but tiny and only partly consistent.** +0.11% rest-of-day ≈ ~0.85 SPY points — far too small to survive a 0DTE debit's documented **9:1 adverse ratio + same-day theta**. (The horizon sweep already showed even 1DTE is a coin-flip; 0DTE is worse.)
2. **The bot's bear 0DTE entry looks backwards / non-robust** — it buys `bear_debit` on a downside OR break, but downside breaks *reverted up* in 2 of 3 years. Anti-edge or noise, not a reliable short.
3. **The only mildly-consistent theme is "buy weakness"** (gap_down and break_down tend to revert *up*) — reinforcing the **oversold mean-reversion** edge already found, not a momentum-breakout edge.

**Decision:** do not build Phase 2. The underlying signal is too weak/inconsistent to monetize at 0DTE. Falsification-first gate: Phase 1 didn't clear → stop, bank the negative result.

## Bigger picture (Studies A + B together)

- **A (horizon sweep):** directional edge needs ~5 days; 1-3DTE weak; 5-7DTE sweet spot.
- **B (opening range):** no robust 0DTE *intraday* directional edge either.
- → **0DTE/1-3DTE directional is genuinely closed.** Small timeframes should be **premium-selling (iron condor)**; directional belongs at **5+ DTE via the oversold dip-buy.**

## Deeper dive (2026-06-07) — OR-window sweep + VWAP + vol gate (Study C)

Re-examined per request rather than treating Phase 1 as closed: swept the OR window, added the VWAP filter (the bot's real `intraday_backtest` entry is OR-break **AND** VWAP-aligned), and gated by VIX.

**1. VWAP adds nothing.** `break_up_vwap` is *identical* to `break_up` (same n, same return) at every window — whenever price closed above the OR-high, it was already above VWAP. The bot's "OR + VWAP blend" is effectively just the OR breakout; the VWAP confirmation is redundant.

**2. The OR-breakout edge is window-fragile — a noise tell.** `break_up` rest-of-day cond-mean by window: **5-min +0.10%, 15-min +0.11%, 30-min −0.07%, 60-min −0.04%.** It *flips sign* on an arbitrary window choice. A real edge wouldn't; this is the multiple-comparisons trap in action. The 15-min "edge" is not robust.

**3. Vol gate (Study C) — the one real, coherent signal.** Splitting the 15-min arms by VIX:

| Arm | calm (VIX<18) | high (VIX≥18) |
|---|---|---|
| break_up (momentum) | +0.045% (n50) | **+0.191%** (n40) |
| break_down | **+0.261%** (n39, reverts *up*) | −0.066% (n34) |

Directional **does** pay more with range: breakouts continue ~4× more on high-VIX days; downside breaks **fade up hard in calm markets**. The coherent regime picture: **calm = mean-reversion (buy weakness), high-vol = momentum.** This *explains* everything — the bot's edges (condor + oversold dip-buy) are calm-market mean-reversion; the high-vol momentum window is the regime it deliberately skips (`TRENDING_HIGH_VOL`).

**Net:** still **no tradeable 0DTE directional edge** — even the vol-gated numbers are small (+0.19% ≈ 1.4 SPY pts), thin (n=40), window-fragile, and sliced enough ways (window × arm × VIX) to invite false positives. But the *understanding* gained is real and reusable: **regime decides direction-vs-reversion.** VWAP confirmation is redundant and could be dropped.

## Caveats

2yr only (vs 16yr for the daily studies); 2024-25 was a bull-grind tape, so "breaks/gaps down revert up" may be regime-specific (a real bear could flip it). n=90/73 on the break arms is modest. In-sample. These caveats only *strengthen* the negative conclusion — a weak, regime-dependent in-sample edge is not something to trade.
