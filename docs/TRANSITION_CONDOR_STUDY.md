# Transition-zone condor study — the "sit out" was too cautious

**Question (2026-07-27):** on a choppy day with VIX in the 18-22 "transition"
band (like today, VIX ~20), the bot says "reduced condor or sit out," and I'd
advised sitting out. Is that right, or is there a conservative condor with real
edge here? Learn where discipline ends and missed opportunity begins.

Study: `backtests/transition_condor_study.py` — condors on choppy (not-trending)
days with VIX 18-22, 2018-present, short-delta × DTE grid, OOS era split, 10%
haircut, vs the calm-band (VIX<18) baseline.

## Result: the transition band is a GOOD condor environment

**Every single variant passes** — all 12 delta×DTE combos, in both eras, *and*
under the 10% haircut. Selected (10% haircut, the number that matters):

| short delta | DTE | n | win% | avg | 2018-22 | 2023+ | verdict |
|---|---|---|---|---|---|---|---|
| 0.20 | 7 | 241 | 73% | +$21.72 | +$22.34 | +$21.09 | PASS |
| 0.20 | 45 | 241 | 70% | +$17.89 | +$16.17 | +$19.63 | PASS |
| **0.15** | **7** | 241 | **77%** | **+$19.98** | +$21.23 | +$18.73 | **PASS** |
| **0.15** | **45** | 241 | **72%** | **+$20.20** | +$16.69 | +$23.73 | **PASS** |
| 0.10 | 7 | 241 | **82%** | +$14.14 | +$16.10 | +$12.17 | PASS |
| 0.10 | 45 | 241 | 77% | +$17.06 | +$12.17 | +$21.98 | PASS |
| *(ref)* calm VIX<18 | 45 | 542 | 69% | +$4.81 | **−$3.26** | +$12.48 | **fail-OOS** |

Read the last row: the **calm-band 0.20/45DTE condor FAILS OOS under haircut**
(negative old era) — while *every transition-band variant passes and pays 3-4×
more per trade*. The intuition that elevated vol is worse for condors is wrong
here: VIX 18-22 pays richer premium, and in *choppy* (non-trending) tape that
premium gets harvested. The "sit out / half size" stance left real edge on the
table.

## The conservative play that fits

Going further OTM does exactly what you'd hope — **trades some average P&L for a
higher win rate and more breathing room**:

- **0.15-delta condor** = the sweet spot: 72-77% win, ~$20/trade under haircut,
  every DTE passes. More room than the standard 0.20 for the same edge.
- **0.10-delta** = the defensive extreme: up to 82% win, collects less (~$14-17),
  still robust. Note the far-OTM breach tail is *larger* when it does breach
  (worst −$429) because the credit is thin against a full-wing loss.
- **7 DTE works here** (unlike the BWB, whose short-DTE failed) — a conservative
  7DTE 0.15-delta condor in VIX 18-22 chop is a legitimate, backtested play.

## Honest caveats

1. **Single-pass structural result** (robust across the whole grid + haircut,
   which is strong — but not yet walk-forward / forward-paper proven). Same bar
   as every lead: it earns a forward test before real money, not a slot.
2. **Rising vs falling vol not isolated.** The study covers the VIX 18-22 band as
   a whole. TODAY specifically has vol *rising* (18.6→19.9), which is the riskier
   flavor — vol *expansion* is the one thing that hurts condors. So even this
   validated play carries more risk on a rising-vol day than on a stable one.
3. **Real breach tails** (−$270 to −$430). Defined-risk, but not risk-free.

## What this changes

- **My "sit out" call was too cautious** — the honest correction the study was
  meant to find. The transition band is tradeable with a conservative condor.
- **Today's IVR-gate removal already re-enables these trades**: with the neutral
  IVR veto gone, the bot now builds a condor on choppy_transition days too (it
  reads direction=neutral there). So this isn't just theory — the bot will start
  taking transition-band condors on its own.
- **Candidate enhancement (not deployed):** in choppy_transition, use 0.15-delta
  shorts instead of the standard 0.20 — same edge, higher win rate, more room.
  Warrants a forward test before changing the live structure.
