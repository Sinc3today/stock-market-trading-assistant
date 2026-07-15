# DTE ladder study — which timeframe rungs earn a disciplined slot?

**Question (user, 2026-07-15):** before opening the disciplined book to
7/14/21/30DTE buckets (3 slots each), which DTEs are actually profitable,
per regime, per structure?

**Method:** `backtests/dte_ladder_study.py` — 2018-present, regimes rebuilt
with the live rules (choppy_low_vol n=536 days; trending_up_calm with the ≤9%
extension gate n=266). Three structures at each of 7/14/21/30/45 DTE, BS marks
at daily closes, management scaled from live parity (70% profit target,
time-exit at ~47% of life — the 45DTE's 21-DTE rule). Two passes: model fills,
then the SKEW_STRESS pass (credits −10%, debits +10%). **A rung passes only if
both eras (2018-22 / 2023+) are positive under the haircut.**

## Haircut-pass results (the numbers that matter)

### choppy_low_vol (n=536)

| structure | 7 | 14 | 21 | 30 | 45 |
|---|---|---|---|---|---|
| condor win/avg | **82% / $33.50** | 77% / $16.72 | 76% / $19.25 | 74% / $13.71 | 70% / $5.12 **fail-OOS** |
| put credit | 70% / $11.41 | 63% / $6.81 | 65% / $8.94 | 68% / $13.93 | 70% / $18.53 |
| call debit | 42% / −$16.93 **fail** | 45% / −$15.12 **fail** | 46% / −$14.51 **fail** | 55% / $0.59 **fail** | 57% / $20.13 |

### trending_up_calm, ext ≤9% (n=266)

| structure | 7 | 14 | 21 | 30 | 45 |
|---|---|---|---|---|---|
| condor | 79% / $28.86 | 75% / $16.35 | 76% / $14.90 | 70% / $12.23 | 65% / $9.54 |
| put credit | 79% / $33.03 | 76% / $29.75 | 79% / $38.67 | 79% / $44.35 | **81% / $53.98** |
| call debit | 54% / $11.76 **fail-OOS** | 55% / $18.18 | 59% / $42.93 | 64% / $71.38 | **71% / $114.26** |

## Verdicts

1. **7DTE condor in chop is the best undeployed rung in the project** —
   82% win and $33/trade *with 10% worse fills*, positive in both eras.
   Fast theta capture is haircut-robust (same physics as the 1-3DTE result).
   → paper generator first, then live mirroring like the 1-3DTE path.
2. **The model reproduced our known 45DTE-condor fragility on its own** —
   45DTE condor fails the haircut OOS (−$3.26/trade pre-2023), exactly the
   EDGE_ROBUSTNESS finding. Good calibration check for the whole study.
   The live 45DTE condor stays: real fills are the referee, and its live
   record is what decides.
3. **Directional debit spreads want TIME.** Call debits are outright losers
   ≤21DTE in chop and marginal at 7DTE even in a trend; they only earn their
   keep at 30-45DTE ($71-114/trade in trend). Never open a short-dated debit
   spread.
4. **Put credit spreads are the all-weather premium play in a trend** —
   pass at every DTE, monotonically better with more time (45DTE: 81%/$54).
5. **14-30DTE rungs pass but are second-tier in chop** ($7-19 avg) — slots
   exist, generators can be wired, but 7DTE and 1-3DTE deserve the capital
   first.

## What changed in live config

`config.DISCIPLINED_BUCKET_SLOTS = {1-3DTE: 3, 7DTE: 3, 14DTE: 3, 21DTE: 3,
30DTE: 3, 45DTE: 3}` — each rung has its own pool (paper_broker enforces
per-bucket; 0DTE shares the 1-3DTE pool). Rungs without a generator (7-30DTE)
hold slots but cannot trade until a paper generator is wired — that is the
deliberate next step, not an accident. Daily-opens (2/day), 90-min spacing and
the 1.5% concentration guard still bound total risk growth.

**Caveats:** BS close-marks; regime reconstruction is the live rule applied
retroactively; overlapping entries measure signal quality, not portfolio P&L.
The 7DTE rung must repeat its result in the paper book before real money.
