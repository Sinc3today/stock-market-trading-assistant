# Broken-wing butterfly study — a defined-risk directional lean

**Question (user, 2026-07-18):** have we looked at ratio spreads? We hadn't. A
naked ratio spread has an uncapped tail, which breaks the defined-risk rule the
whole project (and the Tradier auto-exec plan) is built on. So the defensible
cousin tested here is the **broken-wing butterfly (BWB)** — a ratio-spread-like
wide profit zone with a *hard, capped* max loss because it stays fully long a
far wing.

**Structure:** PUT broken-wing butterfly, bullish/neutral lean (the only lean
that fits our tradeable regimes — a call BWB leans bearish, which we never
trade). `+1 put K_hi` (near money, narrow $5 upper wing), `-2 put K_mid`
(0.30-delta short body), `+1 put K_lo` (far OTM, wide $10 lower wing). The wide
lower wing cheapens it toward a credit; the extra distance is the "break".
It profits when SPY is flat-to-up (theta + drift), keeps the credit above K_hi
with no upside risk, and only loses on a hard drop *through* the body —
defined the whole way down.

**Method:** identical to DTE_LADDER / MAGNET — SPY+VIX 2018-present, live regime
rules, r=0 BS marks (sigma=VIX), same 70%-target / time-exit management, **plain
0.20Δ/$5 condor as the side-by-side benchmark**, OOS era split (2018-22 vs
2023+, both must be positive to PASS), and a 10% fill-haircut stress pass.
Code: `backtests/broken_wing_study.py`.

## Result — the honest verdict

**In chop (`choppy_low_vol`): the condor strictly dominates. BWB does not belong
here.** At every DTE the condor wins more often and pays more, and the BWB
**fails OOS** (negative in the 2018-22 era, positive only in 2023+) — the exact
period-dependence we reject. Under the haircut the BWB fails every chop rung.
The BWB's wide-wing tail also bites harder (worst chop loss −$608 vs condor
−$395).

**In the trend (`trending_up_calm`): the BWB has a real edge at longer DTE — and
it beats the plain condor there.** This is the directional-lean-in-a-trend gap
we've circled for months, and the BWB is the first structure to survive the full
gauntlet in it.

Trending_up_calm, **under the 10% haircut** (the number that matters), both eras
positive:

| structure | DTE | n | win% | avg/trade | 2018-22 | 2023+ | verdict |
|---|---|---|---|---|---|---|---|
| condor | 30 | 266 | 70% | $12.35 | +$21.07 | +$1.06 | pass (thin new-era) |
| condor | 45 | 266 | 65% | $9.34 | +$14.56 | +$2.60 | pass (thin new-era) |
| **BWB** | **30** | 266 | 81% | **$20.67** | +$8.49 | +$36.42 | **PASS** |
| **BWB** | **45** | 266 | 85% | **$29.53** | +$13.56 | +$50.18 | **PASS** |

At 30–45 DTE in the trend the BWB pays **~2–3× the condor per trade under
haircut**, with a *higher* win rate and — unlike the condor — a healthy new-era
(2023+) number rather than a thinning one. The lean is doing real work: in a calm
uptrend the extra long-a-wing structure captures drift the symmetric condor
gives away.

**But the short-DTE BWB fails.** At 7/14/21 DTE in the trend the BWB fails OOS
under haircut (7DTE even goes slightly negative overall). Short-DTE income stays
the condor's job; the BWB only earns its keep at 30–45 DTE where the directional
lean has time to pay.

### Magnet dimension (BWB 7DTE in chop by |spot−MA20|)

Echoes the condor magnet finding — stretched-entry days are better (>1.5%
stretched: 76% / +$27.64, both eras positive) — but this is a within-chop tilt,
and chop is where the BWB loses anyway, so it changes nothing about deployment.

## What this means

1. **The BWB is a genuine lead — the first directional-lean structure to pass
   OOS + haircut in `trending_up_calm`.** Specifically the **30DTE and 45DTE**
   rungs, where it beats the plain condor. That's a real answer to "buying low /
   riding the wave" within the defined-risk rules.
2. **Not a chop trade, not a short-DTE trade.** Condor stays the income engine;
   BWB is a *trend-regime, longer-DTE* complement, not a replacement.
3. **Caveats before anyone gets excited:**
   - **One parametrization** (0.30Δ body, 5/10 wings). A single-pass structural
     result, exactly the kind we've watched evaporate under a robustness sweep.
     Needs a delta × wing-ratio sweep before it's trustworthy.
   - **Bigger tail.** Worst-case loss (~−$300 to −$390 in trend, up to −$600 in
     chop) exceeds the condor's. The extra short contract also means real
     slippage is likely a touch worse than the flat 10% haircut models.
   - **Naked ratio spreads remain rejected** — this result is *only* about the
     capped-risk BWB.

## Parameter-robustness sweep (2026-07-18) — the edge is NOT a knob artifact

`run_sweep()` varied the short-body delta (0.25 / 0.30 / 0.35 / 0.40) against
four wing ratios (3/8, 5/10, 5/15, 3/10) — 16 parametrizations — over
`trending_up_calm` at 21/30/45 DTE, **under the 10% haircut**, each judged on the
OOS era split. If the trend edge were a lucky single tuning, only the original
(0.30, 5/10) cell would survive. Instead:

| DTE | parameter combos passing both eras (of 16) |
|---|---|
| 21DTE | 13 / 16 |
| **30DTE** | **15 / 16** |
| **45DTE** | **16 / 16** |

The edge is **robust across the grid**, and the failures are all in one coherent
corner — the thinnest-premium configs (0.25 delta + 5/10 wings) at short DTE.
Everything else passes. The gradient is physically sensible, not noisy: a
closer-to-money body (higher delta) and a wider lower wing collect more and pay
more in a calm uptrend.

**But higher average P&L is partly bought with a bigger tail — read the sweep
for risk, not just return.** The eye-catching cells (0.40 delta, 5/15 wings:
avg +$88/trade at 45DTE) get there by widening the loss wing: their worst-case
realized loss runs −$600 to −$800. The honest lens is the *lower-upper wing
differential*, which sets max loss: 3/8 and 5/10 (differential 5) keep the tail
at ~−$285 to −$390 — condor-comparable — while 5/15 and 3/10 (differential 7–10)
balloon it.

**Risk-adjusted sweet spot: short delta 0.30–0.35, wings 3/8, 30–45 DTE.**
Representative (haircut, both eras):

| delta | wings | DTE | win% | avg | worst | 2018-22 | 2023+ |
|---|---|---|---|---|---|---|---|
| 0.35 | 3/8 | 45 | 83% | $48.31 | −$301 | +$32.44 | +$68.83 |
| 0.35 | 3/8 | 30 | 83% | $41.53 | −$284 | +$31.38 | +$54.65 |
| 0.30 | 3/8 | 45 | 84% | $40.73 | −$307 | +$25.27 | +$60.73 |

These beat the plain condor's trend numbers (~$9–12/trade under haircut) by 3–5×
with a *comparable* tail and a higher win rate — the strongest risk-adjusted
result in the sweep. Going to 0.40 delta juices the average further but pushes
the short body close to the money (more gamma/assignment risk near expiry), so
0.35 is the prudent ceiling.

**One honest caveat the OOS split only partly covers:** this is a bullish-lean
structure with embedded long-equity-drift beta. It survived 2018-22 (which
includes Q4-2018, the 2020 crash, and the 2022 bear) *in-sample*, which is
real evidence — but its protection against a sustained downturn is ultimately
the **regime gate** (it only fires when SPY is >200MA with calm VIX), not the
structure itself. If the regime classifier is late to flag a top, this bleeds.

## Verdict & next step

The BWB has **survived the full gauntlet**: per-regime, OOS era-split, fill
haircut, *and* a parameter-robustness sweep. It graduates from "suspected knob
artifact" to "warrants a live forward-paper test" — the same bar the 7DTE condor
cleared before it.

Recommended paper-test config: **put BWB, 0.35 delta body, 3/8 wings, entered in
`trending_up_calm`, laddered at 30 and 45 DTE, 1-lot.**

Proposed path (not yet built — needs go-ahead, it touches the live learning loop):
- A **forward paper generator** (`learning/broken_wing_forward.py`) mirroring
  `seven_dte_forward`: opens on trend-regime days, idempotent, `candidate` book,
  promotion bar fixed at creation (n≥15, win≥70%, avg>$20, no loss beyond
  max_loss). Prove it live before it ever touches the disciplined book.

Nothing is deployed. The sweep earns the BWB a *forward paper test*, not a slot.
