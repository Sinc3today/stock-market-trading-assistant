# The DTE ladder — the right exit for every rung

**2026-09-06.** `backtests/dte_ladder_exit_study.py`

The exit-timing sweep found the 45DTE condor's configured 21-DTE close testing
negative after commissions. I refused to act on it, because mean P&L cannot
settle that question: holding a condor into expiry week is a **gamma bet**, and
the case for 21 DTE was never about the average — it is about the tail.

So this asks it properly for every rung (45 / 30 / 21 / 14 / 7), reporting
median, 5th-percentile, worst, win/loss ratio, mean-over-σ, and **max adverse
excursion during the hold** — the path pain an endpoint number cannot show.
0.20-delta condor, $5 wings, calm regime, 2018+, 10% haircut, commissions,
live 70%-target rule.

Cross-checked against `exit_timing_sweep`: 7DTE@1 reproduces (+$38.52 both),
45DTE@21 reproduces (−$1.41 vs −$2.78). The machinery agrees with itself,
which is the check that caught the last bug.

A test written for this study then caught another one: `simulate` used to
return a P&L when the data ran out **before** the exit date, silently
truncating every trade in the final `dte` days of the sample and reporting it
as if it had completed. Fixed; it moved the headline figures by under $1.

## Finding 1 — every rung wants to be held far longer than we hold it

| rung | live close | best close | live avg | best avg |
|---|---|---|---|---|
| **45DTE** | **21** | **7** | **−$1.41** | **+$14.31** |
| 30DTE | — | 3 | — | +$28.76 |
| 21DTE | — | 2 | — | +$42.06 |
| 14DTE | — | 1 | — | +$49.47 |
| 7DTE | 1 | 0 | +$38.52 | +$55.46 |

Ranked on **mean/σ**, not raw mean. The pattern is monotone and consistent: the
optimal close sits near `dte/6`, nowhere near the `21/45 ≈ 0.47` fraction that
was being scaled around.

### The tail question, answered

Holding longer **does** cost tail — the concern was legitimate:

| 45DTE close at | avg | p05 | worst | MAE | mean/σ |
|---|---|---|---|---|---|
| **7** | **+$14.31** | −$247 | −$364 | −$91 | **+0.117** |
| 21 (live) | −$1.41 | −$190 | −$327 | −$64 | −0.018 |

The 5th-percentile loss worsens 30% and the worst case 11%. But the strategy
goes from **losing to winning**, and risk-adjusted return improves decisively.
The gamma-risk instinct behind 21 DTE is real — it is just **overpriced**. We
were paying ~$16/trade of expected value to shave $57 off a 1-in-20 loss.

## Finding 2 — shorter rungs dominate, and there is a mechanism

Ranked by mean/σ at each rung's best exit:

**7DTE (0.431) > 14DTE (0.413) > 21DTE (0.339) > 30DTE (0.219) > 45DTE (0.117)**

Path pain falls the same way (MAE −$46 at 7DTE vs −$91 at 45DTE). **The rung we
actually trade in the disciplined book is the worst on the ladder.**

The mechanism is signal decay, and it is testable: our entry filter reads
*today's* regime, which predicts the next week far better than the next 45
days. If that is the cause, the filter's value should shrink with horizon.

Filter value = calm-population average − all-days average:

| rung | 2018–22 | 2023+ |
|---|---|---|
| 45DTE | **−$22.02** | +$1.63 |
| 30DTE | **−$21.78** | +$8.91 |
| 21DTE | −$10.80 | +$25.50 |
| 14DTE | −$7.65 | +$26.36 |
| **7DTE** | **+$3.06** | +$26.40 |

Confirmed, and then some: at 45 and 30 DTE the calm filter is not merely
uninformative, it is **actively harmful**. Selecting the calmest days means
systematically selling the *cheapest* premium, and over 45 days vol
mean-reverts anyway — you took the same risk for less money.

## Finding 3 — the caution that reframes all of it

**The calm filter helps in 2023+ and hurt in 2018–22 at every rung except
7DTE.** 2023+ has been a calm bull market; a calm filter selects the best days
of an already-good period. That is regime luck, not proven edge.

Meanwhile the **unfiltered** condor is strikingly era-stable:

| rung | all-days 2018–22 | all-days 2023+ |
|---|---|---|
| 45 | +$28.39 | +$21.81 |
| 30 | +$35.56 | +$33.07 |
| 21 | +$32.78 | +$34.31 |
| 14 | +$37.25 | +$40.62 |
| 7 | +$39.14 | +$40.61 |

Old and new within a few dollars at every rung. **Era stability is the
robustness signal we actually trust**, and the unfiltered book has far more of
it than the filtered one.

This does *not* mean "trade every day." The all-days population includes
February 2020 and 2022, and a daily-close model with BS marks will understate
crash-gap risk badly. It does mean the regime filter is doing much less work
than we believed, and its 2023+ performance should not be read as validation.

## Verdicts per rung

| rung | verdict |
|---|---|
| **45DTE** | The live combination — 45DTE + calm filter + 21-DTE close — is the **worst cell in the matrix**. Every alternative tested beats it. |
| **30DTE** | Better than 45, still filter-harmed in the old era. No live rung; nothing to change. |
| **21DTE** | Strong (+$42.06, mean/σ 0.339) and untested live. The most interesting **unbuilt** rung. |
| **14DTE** | Stronger still (+$49.47, 0.413), and the best win/loss ratio on the board (0.74 at close-2). |
| **7DTE** | Best risk-adjusted rung, and the only one where the calm filter helps in **both** eras. `CLOSE_DTE 3 → 1` already shipped; 0 tests better but adds assignment risk. |

## What I am NOT doing

Not changing the disciplined 45DTE book on this evidence. It is one model:
daily closes, BS marks, no intraday path, no assignment modelling, a flat 10%
haircut standing in for real fills. The finding is strong enough to **test**,
not strong enough to **deploy** — and the whole point of the gates is that
those are different thresholds.

## Recommended next step

Run the change as a **paper candidate against the incumbent**, head to head,
same days, and let live fills referee:

  * `45DTE @ close-10` (not 7 — keeps a gamma buffer, captures ~87% of the
    gain, better p05) versus the live `45DTE @ close-21`.
  * Pre-register the bar now: **n ≥ 20 paired closes, candidate beats incumbent
    on mean AND on 5th-percentile loss**, before anything moves in the
    disciplined book.

Paired on identical entry days, the comparison needs far fewer samples than an
absolute test, because market noise is differenced out.

Open question worth its own study: the **21DTE and 14DTE rungs are unbuilt and
test better than anything we run.** That is the largest unexplored gain on the
board.
