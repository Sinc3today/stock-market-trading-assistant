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

## Caveats

2yr only (vs 16yr for the daily studies); 2024-25 was a bull-grind tape, so "breaks/gaps down revert up" may be regime-specific (a real bear could flip it). n=90/73 on the break arms is modest. In-sample. These caveats only *strengthen* the negative conclusion — a weak, regime-dependent in-sample edge is not something to trade.
