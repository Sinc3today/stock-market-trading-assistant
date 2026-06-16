# Event-Condor Study (CPI extension + breach mitigation) — the FOMC lead doesn't hold up

**Date:** 2026-06-16 · **Module:** `backtests/event_condor_wf.py` · Research only.
**Follows:** `docs/FOMC_CONDOR_STUDY.md` (FOMC condor looked like +$33/event, 79% win).

Per the ask, two extensions of the FOMC-condor lead: **(1) does it generalize to CPI**, and
**(2) can the breach tail be mitigated** by widening the short strikes (shorts at 1.0 / 1.25 / 1.5×
the expected move). Real Polygon prices, $5 wings, held to expiry, net of slippage.

## Results
| placement | FOMC (n=14) | CPI (n=23) | ALL |
|---|---|---|---|
| **1.0× EM** | 79% win, **+$33**, breach 14% | 61% win, **−$30**, breach 22% | 68% win, −$6 |
| 1.25× EM | 86% win, +$4 | 78% win, −$7 | 81% win, −$3 |
| 1.5× EM | 86% win, −$17 | 82% win, +$10 | 83% win, −$1 |

## Two findings, both negative for the thesis
1. **CPI does NOT replicate FOMC.** At the natural placement, selling premium into CPI **loses
   (−$30/event, 22% breach)** — the *opposite* of FOMC. CPI moves clear the priced straddle more
   often (premium is fairly/under-priced). So "sell premium into events" is **FOMC-specific, not a
   general event edge.** A sibling event contradicting it undercuts the mechanism.
2. **Breach mitigation fails.** Widening the shorts made FOMC monotonically **worse**
   (+$33 → +$4 → −$17): win-rate rises but the credit shrinks faster than breaches fall, breach%
   stays ~14% (idiosyncratic big moves blow through even 1.5× shorts), and the *worst* loss grows
   (−$408 → −$454, less credit cushion). You can't engineer the tail away with strike placement.

## Verdict — downgrade the FOMC lead
The FOMC +$33 stands only **in isolation, in-sample, n=14**. It **doesn't generalize** (CPI is
negative) and **can't be hardened** (wider shorts worse). Across FOMC+CPI at every placement the
combined mean is ≈ break-even-to-negative. The honest read: **event premium is essentially
efficiently priced; the FOMC number is most likely a small-sample anomaly, not a deployable edge.**
This re-confirms the locked **"skip events (defense)"** rule — now stress-tested from the
sell-premium side too (straddle magnitude → FOMC condor → CPI → strike sweep all converge on
"efficient / no robust edge"). Good falsification outcome: the intuition was worth testing, the
testing said no.

**Not pursuing further** unless a much longer option-history window becomes available (the binding
constraint is ~2 yr / n≈14 per event type).
