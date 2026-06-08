# Overbought-Short Signal Study (negative)

**Date:** 2026-06-07 · **Module:** `backtests/overbought_short_study.py` · Research only.

## Question
Mirror of the oversold dip-buy: does SPY mean-revert DOWN after a fresh RSI>70 overbought
cross (so a bear-debit short has an edge), the way it bounces UP after RSI<30?

## Result — FALSIFIED (no short edge)

| Horizon | n | SPY fwd return | % down | read |
|---|---:|---:|---:|---|
| 3d | 107 | **+0.10%** | 42% | keeps rising |
| 5d | 107 | **+0.06%** | 39% | keeps rising |
| 10d | 106 | **+0.25%** | 34% | keeps rising |

SPY's forward return after overbought is **positive** at every horizon (only 34–42% of days
go down). It does NOT revert; it keeps drifting up. A short loses. (The "short_edge" metric
is positive only because overbought days rise *slightly less* than the +0.15–0.51% baseline —
they still rise.)

## Conclusion — the equity-index asymmetry

Mean-reversion works **long only** on SPY. Dips get bought (RSI<30 → +1.4–2.0% bounce, the
dip-buy edge); rallies do NOT get sold (RSI>70 → still +0.1–0.25%). SPY's structural upward
drift kills mean-reversion shorts. **Do not pursue an overbought-short.** A *short* edge, if
one exists, must come from **momentum in downtrends** (high-vol regime), not from selling
overbought — which points directly at the trend-follow study (next).
