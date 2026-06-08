# Event-Relative Timing Study ("buy rumor, sell news") — small / negative

**Date:** 2026-06-07 · **Module:** `backtests/event_timing_study.py` · Research only.
**Scope:** computable monthly events — NFP (1st Friday), OPEX (3rd Friday), 2010-2026.

## Result

| Event | n | pre run-up | post reaction | post-edge vs baseline | post % up | ΔVIX (T→T+1) |
|---|---:|---:|---:|---:|---:|---:|
| NFP | 196 | +0.24% | +0.22% | +0.06% | 56% | **+0.64** (vol up) |
| OPEX | 197 | **−0.27%** | +0.23% | +0.08% | 59% | +0.13 |

- **No "sell the news" reversal and no vol crush** in these events. NFP: SPY keeps drifting
  up and VIX *rises* after. OPEX: drift *down* into expiration then bounce after (the known
  gamma pin/unpin), but post-edge is only +0.08%.
- Effects ~0.06–0.08% above baseline — real but far too small to trade after option costs.
- The post-OPEX bounce is just another instance of **buy-the-dip** (already captured by the
  dip-buy / breakdown triggers).

## Conclusion
No tradeable event-timing edge from NFP/OPEX — **keep skipping events (defense) as-is.**

**Untested (the real question):** the vol-crush "sell the news" pattern lives in **FOMC/CPI**
(big scheduled-uncertainty events), which need a historical date table (NFP/OPEX are
computable; FOMC/CPI are not). This study is a lower bound. If event-offense is worth
pursuing, the follow-up is: build a historical FOMC/CPI date table, then test whether condors
the day-after harvest a post-event IV collapse.
