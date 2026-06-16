# FOMC Iron-Condor Study — selling premium into FOMC beats skipping (thin edge, breach tail)

**Date:** 2026-06-16 · **Module:** `backtests/fomc_condor_wf.py` · Research only.
**Follows:** `docs/EVENT_STRADDLE_STUDY.md` (FOMC straddles are over-priced — move clears them only 27%).

## Setup
For each FOMC: sell a defined-risk iron condor the prior close, **shorts at the expected-move
(ATM-straddle) breakevens**, $5 wings, held to expiry. Entry credit from **real Polygon option
prices** (4 legs); expiry P&L = credit − intrinsic at the SPY close. **Net of $0.05/leg slippage.**
Baseline = skip (current bot behavior) = $0. 14 FOMCs priced, 2024-07 → 2026-04.

## Result
| | n | win% | mean/FOMC | total |
|---|---:|---:|---:|---:|
| **Full** | 14 | **79%** | **+$33** | +$458 |
| In-sample (first 60%) | 8 | 75% | +$36 | — |
| Out-of-sample (last 40%) | 6 | 83% | +$28 | — |

**Beats skip, and the edge holds in both IS and OOS** (consistent, not a single-window artifact).

## The honest shape — win-small / lose-big
- 11 wins of ~$96–130 (keep the credit) + 3 losses: −$34, **−$408**, **−$387**.
- The two big losses are **near-max breaches** — SPY blew through a short strike (Dec-2024: −$408; Mar-2026: −$387). 14% breach rate.
- So expectancy is **positive but thin (+$33)** and **tail-dependent**: the two breaches nearly erased the 11 wins. One more clustered breach flips the math.

## Verdict
**The best event finding we have** — it's direction-agnostic, defined-risk, net-of-costs positive,
IS/OOS consistent, and it's *incremental* (FOMC days the bot currently skips). It vindicates the
"there's a pattern here" intuition. **But it is NOT yet deployable:**
- **Small sample (n=14)** — ~2 yr is all the option history we have; IS/OOS is directional, not robust.
- **Thin, breach-tail-dependent** edge — +$33 mean risking ~$400 to make ~$110.
- One strike-placement, fixed wings, held-to-expiry (no intra-event management).

## Warranted next steps (before any real-money use)
1. **Breach mitigation is the highest-value lever** — the whole edge lives or dies on the ~14% tail.
   Test: wider shorts (higher win%, less credit), or a defined exit if a short strike is breached
   intra-event (hard — FOMC moves fast), or smaller size.
2. **More events** — extend option history if a longer window becomes available; add CPI (vetted dates).
3. If it survives, wire as a *learning-book* paper play first (sell a half-size FOMC condor), measure
   live for several FOMCs before it touches the disciplined book — never auto-enable on n=14.
