# Is "calm" a true label? — the hypothesis was wrong, and the answer redirects us

**Built 2026-09-06** as `learning/calm_calibration.py`, backfilled by
`backtests/calm_calibration_backfill.py`.

## The hypothesis I set out to confirm

August 2026 hurt: SPY ran +5.7% in four sessions while VIX stayed under 18, and
the live condors' short calls were run through. My reading was that the
classifier had **mislabelled a directional thrust as calm** — implied vol low,
realised vol high — and that "calm" was therefore systematically underpricing
risk.

The instrument scores that claim directly. On every calm-labelled day it
records the move VIX *implied* over 5 sessions and, later, the move that
actually happened. Ratio = realised / implied. Breach = a move past **0.85
sigma**, where a 0.20-delta short strike sits (not 1 sigma — 0.85 is the line
that actually hurts).

## The result — hypothesis DISPROVED

| bucket | n | breach | median ratio | verdict |
|---|---|---|---|---|
| **CALM (ADX<32, VIX<18)** | 552 | **20.1%** | **0.46** | calibrated |
| not calm | 1573 | 27.7% | 0.54 | calibrated |
| calm · 2018–2022 | 264 | 21.6% | 0.50 | calibrated |
| calm · 2023+ | 288 | 18.8% | 0.42 | overpriced |

**"Calm" is an honest label.** On calm days realised vol comes in at a median
of **0.46× implied** — less than half the move VIX was pricing. That gap *is*
the premium-selling edge, and it is why the strategy works at all. Calm days
also breach **less** often than non-calm days (20.1% vs 27.7%), so the label
is doing exactly the discriminating it claims to.

The August event was not a labelling failure. It was a **20%-of-the-time
outcome that we had priced for**, landing on a structure that could not absorb
it.

## The row that matters most

| bucket | n | breach | median ratio |
|---|---|---|---|
| **calm AFTER a >3% week** | 31 | **6.5%** | **0.27** |
| calm after a quiet week | 376 | 23.1% | 0.50 |

**Entering after a big move is the SAFEST setup measured here — a 6.5% breach
rate against 23.1% after a quiet week.** A market that has just spent
directional energy is markedly less likely to spend more.

This independently replicates `docs/VELOCITY_GATE_STUDY.md`, which found the
same thing from a completely different angle (P&L by trailing move, rather
than realised-vs-implied vol). Two unrelated methods, same conclusion: **the
instinct to stand aside after a thrust is backwards.** That instinct has now
been tested twice and failed twice.

## What it says about the higher end of "calm"

| VIX band | n | breach | median ratio |
|---|---|---|---|
| 0–13 | 73 | 17.8% | 0.48 |
| 13–15 | 168 | 19.6% | 0.50 |
| 15–16.5 | 176 | 22.7% | 0.49 |
| **16.5–18** | 135 | **18.5%** | **0.39** |

The *top* of the calm band is the best-priced part of it — consistent with
`docs/TRANSITION_CONDOR_STUDY.md`, which found VIX 18–22 chop is good condor
territory. Low VIX is not the same thing as safe; **well-paid** is what matters.

## Where this redirects the 7DTE question

If the label is honest and the entry timing is fine, the August damage was
structural. The instrument quantifies exactly why, by asking how often a calm
week touches a given distance:

| shorts at | ≈ delta | touched intraweek |
|---|---|---|
| **0.85 sigma** (today) | 0.20 | **25.7%** |
| 1.00 sigma | 0.16 | 13.2%* |
| **1.25 sigma** | 0.11 | **8.2%** |
| 1.50 sigma | 0.07 | 4.7% |

<sub>*endpoint breach; the others are max intraweek excursion.</sub>

**A 7DTE condor at 0.20 delta is under pressure one week in four.** A 45DTE
condor can absorb that — it has weeks of theta left and only needs to be right
at exit, and its worst live loss is −$55. A 7DTE condor has no recovery time:
its entire life *is* one of those weeks, which is how three of them produced
−$300, −$233 and −$215.

Moving the shorts to ~0.11 delta cuts the touch rate **3×**, at the cost of a
smaller credit. Whether that trade is net positive is a question for a study,
not an opinion — see `docs/SEVEN_DTE_STRUCTURE_STUDY.md`.

## Standing use

`job_calm_calibration` runs 16:12 ET on trading days: logs a calm claim, and
resolves any whose 5-session horizon has elapsed. ~100% event rate, zero
capital, no counterfactual trade — it only scores a claim we already make.

**The point to keep:** this instrument was built to confirm a suspicion and
instead refuted it, then handed us a better question. That is what an
instrument is *for*. Had it merely agreed with me, it would have taught
nothing.
