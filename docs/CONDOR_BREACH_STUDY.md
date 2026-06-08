# Calm-Condor Breach-Prediction Study (negative)

**Date:** 2026-06-07 · **Module:** `backtests/condor_breach_study.py` · Research only.

## Question
The calm iron condor wins 82% (265 days, 47 breaches/losers in the 5yr backtest). Can a
grounded vol-expansion early-warning predict the breaches so we skip them?

## Result — FALSIFIED (no usable signal)

| Signal (one clean def) | breaches flagged | skip-bucket win% | keep win% |
|---|---|---|---|
| VIX rising (>5d ago) | 21/47 | 81% | 83% |
| Backwardation (VIX9D>VIX3M) | 0/47 (1 day in 5yr) | — | 82% |
| VVIX rising (>60d MA) | 21/47 | 79% | 84% |

- Each signal flags ~21 of 47 losers — about the **base rate**, i.e., no discrimination.
- Backwardation essentially never occurs in the calm regime (1 day / 5yr) — useless there.
- VVIX is marginally directional (79 vs 84% win) but skipping vvix-rising days drops **100 of
  265 condors (38%)** to lift win-rate only 82→84% — a net loss of total profit.

## Conclusion
The calm-condor breaches are **idiosyncratic surprises**, not foreshadowed by VIX rate-of-
change, term structure, or VVIX. No filter helps; the protection is the defined-risk wings.
Combined with the condor already using IVR≥50 + ~0.20-delta strikes + event blocks, the calm
condor is **at its achievable edge — stop tinkering.** (Caveat: synthetic-payoff backtest;
the conclusion is robust because breaches map to real big-move days regardless.)
