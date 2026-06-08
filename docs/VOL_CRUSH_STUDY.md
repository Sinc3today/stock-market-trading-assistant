# Post Vol-Crush Study — relief rally = buy-the-dip; NOT a condor edge

**Date:** 2026-06-07 · **Module:** `backtests/vol_crush_study.py` · Research only.
**Why empirical:** exact historical FOMC/CPI dates aren't reliably sourceable without an
error-prone hand table, so we test the vol-crush *phenomenon* directly (what those events
cause): a sharp VIX drop (−10%+ over 2 days) from an elevated (≥20) level. 171 events, 2010-2026.

## Result

| Post-crush horizon | SPY fwd (baseline) | % up | realized vol (baseline) |
|---|---|---:|---|
| 3d | +0.37% (+0.15%) | 60% | 1.12 (0.83) — higher |
| 5d | +0.41% (+0.26%) | 60% | 1.19 (0.87) — higher |
| 10d | +0.83% (+0.51%) | 64% | 1.17 (0.90) — higher |

1. **Relief-rally LONG edge:** SPY bounces ~2× baseline (60-64% up) after a vol crush. But
   crushes follow selloffs — this is the **same buy-the-dip "buy weakness" bounce** from the
   VIX side, overlapping the existing dip-buy/breakdown triggers. Confirmation, not new.
2. **NOT a condor edge:** post-crush realized vol stays **elevated** (1.12-1.19 vs 0.83
   baseline). IV falls but the tape stays choppy — *not* calm. So there is **no condor offense
   around vol events**; the "sell the news = sell condors / harvest calm" idea is refuted.

## Conclusion
**No event-offense edge.** Combined with the NFP/OPEX timing study (also nothing), the
event/news angle yields no tradeable edge — keep **skipping events (defense)**. The post-event
window only offers another flavor of buy-the-dip, already captured. (Exact FOMC/CPI dates
remain a possible data follow-up, but the phenomenon test makes a fresh edge unlikely.)
