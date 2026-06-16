# Event-Straddle Study — blanket event-trading is efficient, but FOMC premium is over-priced

**Date:** 2026-06-16 · **Module:** `backtests/event_straddle_study.py` · Research only (first-cut screen).

## Why this study
Prior event work measured **drift** (net direction around events ≈ 0) and post-event realized
vol — both dead. This tests the thing we never tested: **magnitude vs price** — buy the pre-event
ATM straddle with REAL historical option prices and ask whether the realized move clears the
premium. You don't need the direction; only whether the wave beats what you paid. (User's framing:
"just because we don't know the direction of the ocean doesn't mean we can't swim.")

Method: for each FOMC (published calendar) + NFP (computed first Friday), entry = prior trading
close, ATM strike = round(spot), expiry = nearest Friday ≥ event; straddle cost from real Polygon
option aggregates; realized move = |SPY at expiry − strike|. 37 events priced, 2024-07 → 2026-06.

## Result
| Bucket | n | long mean | long median | long win% | read |
|---|---:|---:|---:|---:|---|
| **All events** | 37 | +$0.44 | **−$0.59** | 43% | mean is outlier-driven (Apr-25 tariff NFP +$23); median < 0 and under the cost floor → **efficient/dead** |
| **FOMC** | 15 | −$1.35 | **−$3.64** | **27%** | move clears the straddle only 27% of the time → straddles **systematically over-priced** → SHORT-premium tilt (won 73%, +$1.35, *above* cost floor) |
| **NFP** | 22 | +$1.67 | +$0.81 | 54% | mild *opposite* (long) tilt — fatter tails, move clears the premium just over half the time |

## Conclusion
1. **Blanket "trade events" = dead** (efficiently priced) — confirms the earlier event-timing /
   vol-crush findings. Buying or selling straddles indiscriminately on events has no edge.
2. **The real lead: FOMC premium is over-priced.** The realized move clears the ATM straddle only
   27% of the time, with the short side beating the cost floor. That's a **direction-agnostic,
   defined-risk** angle the prior tests missed (they measured drift, not magnitude, and didn't split
   by event type). It suggests that on FOMC days we should **not skip — sell defined-risk premium**
   (iron condor / credit spread), the OPPOSITE of the current blanket skip.
3. NFP leans the other way (mild long tilt) — weaker, don't act on it.

## Honest caveats (this is a screen, not a green light)
- **Small sample:** FOMC n=15 — too few for a real walk-forward; the consistency (73% short-win,
  solidly negative long median) is suggestive, not proven.
- **In-sample, gross of fees/slippage.** The blanket result died at the cost floor; the FOMC edge
  *exceeds* it, but a real condor pays the bid/ask on 4 legs.
- **Straddle ≠ condor.** This measures the ATM straddle; an iron condor (OTM short strikes) would
  have a *higher* win rate (the move stays inside the wings more often) but a different risk profile
  — the breach risk on the ~27% of FOMC days the move IS big is the thing to size for.
- **Reconciles with prior work, doesn't contradict it:** the dead finding was *post-event realized
  vol* + *drift*; this is *pre-event IV being over-priced relative to the realized move*, FOMC-specific.

## Warranted next step
A proper **FOMC iron-condor study**: real condor pricing into FOMC, walk-forward over the available
events, net of costs, sized for the breach tail — does selling a defined-risk condor into FOMC beat
skipping it? This is the first event finding worth that follow-up.
