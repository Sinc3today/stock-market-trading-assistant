# Donchian Trend-Follow Study (negative for trend; reinforces buy-the-dip)

**Date:** 2026-06-07 · **Module:** `backtests/trend_follow_study.py` · Research only.
**Data:** SPY daily 2010-2026. Edge = forward return vs SPY's baseline drift (not vs zero —
SPY drifts up, so "positive" ≠ edge).

## Result

| Arm | h | cond return | baseline | edge | read |
|---|---:|---:|---:|---:|---|
| breakout_up (close > 50d high) | 10d | +0.24% | +0.51% | **−0.26%** | underperforms drift |
| breakout_up | 20d | +0.45% | +1.02% | **−0.57%** | underperforms drift |
| breakout_down (close < 50d low) | 10d | **+1.65%** (68% up) | +0.51% | (short loses) | SPY BOUNCES |
| breakout_down | 20d | **+2.51%** (70% up) | +1.02% | (short loses) | SPY BOUNCES |

Vol gate (per Study C): breakout_up high-vol +0.39% vs calm +0.21% — momentum is mildly
stronger in high-vol but **still below the +0.51% baseline** (no edge). breakout_down bounces
in BOTH calm (+1.71%) and high-vol (+1.65%, n=73) — even high-vol breakdowns get bought.

## Conclusion — SPY is a mean-reversion (buy-the-dip) instrument, full stop

- **Trend-follow LONG is dead:** breakouts to new highs *underperform* the baseline drift
  (slight reversion). Buying strength loses to just being long.
- **Trend-follow SHORT is dead:** breakdowns to new lows *bounce* +1.6–2.5% (68–70% up) — even
  in high-vol. Shorting breakdowns loses badly; breakdowns are BUYABLE.

This closes the directional map. Every test converges on one truth:

> **The only directional edge on SPY is LONG mean-reversion — buy weakness.**
> Oversold (RSI<30) bounces; 50d-low breakdowns bounce; overbought keeps rising (no short);
> new-high breakouts revert (no momentum). Momentum and shorts do not work on this index.

## Bonus positive — a candidate second buy-the-dip trigger

`breakout_down` (close < prior 50-day low) → +1.65% (10d) / +2.51% (20d), 68–70% up, n=80 over
16yr. That's a strong, more-frequent "buy weakness" signal — a candidate **complementary
dip-buy trigger** alongside RSI<30. Worth a follow-up (overlap with RSI<30, option-priced,
forward-test). Caveat: in-sample, mostly-bull tape — real bear breakdowns would continue
down, so any live use needs the same defined-risk + forward-test discipline as the dip-buy.
