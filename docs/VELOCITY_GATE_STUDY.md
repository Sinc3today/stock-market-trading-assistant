# Realized-velocity gate study — the naive gate fails, the lesson relocates

**Question (2026-08-05):** the classifier read a +5.7%-in-4-days rally as
"choppy_low_vol — sell premium," and the live condors got run over. Should we
gate condor entries on a big trailing move ("SPY moved >X% in N days → be
wary")? Study: `backtests/velocity_gate_study.py` — 45DTE condor in
choppy_low_vol, bucketed by trailing 5-day absolute move, OOS + 10% haircut.

## Result — the gate is NOT warranted (and the gradient runs backwards)

| trailing 5d move | n | win% | avg (haircut) | verdict |
|---|---|---|---|---|
| quiet <1.5% | 374 | 70% | **+$3.07** | **fail-OOS** |
| 1.5–3% | 142 | 71% | **+$14.04** | PASS |
| 3–5% | 25 | — | (n<30) | — |
| **fast >5%** | **2** | — | (n<30) | — |

Two things jump out:

1. **The hypothesis is backwards in the testable range.** *More* recent movement
   (1.5–3%) makes condors *better*, not worse — 71% win / +$14 vs the dead-quiet
   bucket's +$3 that *fails* OOS under haircut. This echoes the magnet study's
   "stretched entries are better" finding: a market that has spent some
   directional energy mean-reverts into the condor. Gating out post-move entries
   would remove the *good* trades.

2. **The extreme case is a genuine rarity: n=2 in 8 years.** A >5% five-day move
   that coincides with VIX already back under 18 — i.e. exactly today — has
   almost no historical precedent. That's structural: a low-VIX regime
   *mechanically excludes* most fast moves, because VIX is usually elevated
   during and right after a thrust. When VIX crushes *this* fast after a move
   (like now), the data barely contains it.

## Why the study can't "see" the harm you felt — and where the lesson really is

The study re-centers the condor on the *current* spot each day, so it answers
"should I **open** a condor after a big move?" — and the answer is *it's fine,
even good*. But your pain wasn't an entry problem. Your condors were opened
*before* the run (07-09, 07-31), and the rally pushed SPY toward their
*existing, lower* short calls. **No entry gate can prevent that** — you can't
filter on a move that hasn't happened yet.

So the lesson relocates:

- **Entry-velocity gate: rejected.** Opening after a move is fine; the quiet
  entries are actually the weak ones. Adding the gate would hurt, not help.
- **The real locus is MANAGEMENT.** An existing condor run over by a
  *subsequent* move is a close/trim decision, not an entry filter. The tools are
  already right: the stop-watchdog (alerts as SPY nears a short strike) plus
  disciplined trimming (the 3-lot → 1 call we made on the live Sep-18 condor).

## The refined version of the original insight — validated

The naive "big move → skip" gate is wrong. But a sharper version of the user's
point survives and is *confirmed by the n=2*: **external / real-world awareness
matters most exactly when you are OUTSIDE the statistical sample.** Today's
fast-move-plus-crushed-VIX setup has 2 historical analogues — the statistics are
effectively *silent*. In those thin-data moments the defensive posture (be wary,
don't pile new risk into a stretched, +10%-extended tape) is right precisely
*because* the stats can't guide you — not as a discretionary override of a
confident signal, but as the correct response to a *low-confidence* one.

## Verdict

1. **No velocity entry-gate.** Data rejects it; quiet entries are the weakest,
   post-move entries are fine-to-good, and the extreme is untestable (n=2).
2. **Management, not entry, is where a fast adverse move is handled** — watchdog
   + trim discipline, already in place.
3. **Keep the "know when you're off the map" tenet:** when a setup has near-zero
   historical precedent (rare regime combinations), treat the signal as
   low-confidence and lean defensive. That's the honest, non-discretionary way
   external reality earns a seat — as a confidence *discount*, not a new trade.
