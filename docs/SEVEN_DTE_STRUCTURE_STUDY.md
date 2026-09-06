# What structure fits 7DTE? — it was never the structure

**2026-09-06.** `backtests/seven_dte_structure_study.py`

> ## CORRECTION — issued 2026-09-06, same day
>
> The first run of this study had a **calendar-vs-trading-day bug**: DTE is in
> calendar days but the price index is trading days, and the exit was resolved
> by walking `DTE - close_dte` **rows** forward. That overshoots by ~40% — a
> 7-row hold is ~10 calendar days, i.e. *past expiry*.
>
> The bug was found by `backtests/exit_timing_sweep.py`, which produced an
> impossible result for the 45DTE condor and forced a re-check. Every number
> below is from the corrected run. **Three original conclusions were wrong:**
>
> | claim as first published | corrected |
> |---|---|
> | close-at-3 DTE **fails OOS** at +$2.27 | **PASSES** at +$11.16 |
> | "the rule destroyed **93%** of the edge" | it cost ~$28/trade — real, but ~72%, and never a failure |
> | "stop-on-touch is **actively harmful**" (+$3.71, fail-OOS) | **PASSES** at +$46.11, and cuts the worst loss from −$427 to −$185 |
>
> The headline direction survived — later close is better, 0.20 delta is best,
> and `CLOSE_DTE: 3 → 1` remains correct and is now worth **+$28/trade** rather
> than the overstated figure. But the retraction on stop-on-touch matters: it
> is *not* harmful, and it is a genuine tail-reducer worth its own study.

## The problem

The 7DTE condor is the worst thing on the books: **n=8 live, 62% win, −$475
net, avg −$59**, and the three largest losses in the entire journal
(−$300 / −$233 / −$215) all came from it during the August thrust.

The instruction was not "retire it" but "find the strategy that fits the
window." So the hypothesis under test was the one
`docs/CALM_CALIBRATION.md` pointed at: **the shorts are too close.** In a calm
week the 0.20-delta short strike is touched 25.7% of the time, and a 7DTE
condor has no time to recover from that. Moving the shorts out to 0.11 delta
cuts the touch rate 3×.

## Result 1 — that hypothesis is wrong too

Calm regime, 2018+, OOS era-split, 10% credit haircut **and** $5.20 commissions:

| shorts at | n | win | avg | verdict |
|---|---|---|---|---|
| **0.20 delta** | 552 | 86% | **+$59.11** | **PASS** |
| 0.16 delta | 552 | 90% | +$48.15 | PASS |
| 0.12 delta | 552 | 92% | +$34.76 | PASS |
| 0.10 delta | 552 | 94% | +$27.89 | PASS |
| 0.08 delta | 552 | 95% | +$20.93 | PASS |

Moving the shorts out raises the win rate all the way to 95% and **lowers the
money every step**. The credit surrendered costs more than the losses avoided.
The current 0.20 delta is the best of the tested set.

Wings: $2 gives too little credit (+$16.06). $10 earns more on average
(+$48.17) but carries a −$947 tail against −$461, so **$5 stays the choice** —
the extra return does not pay for doubling the worst case on a 1-lot book.

## Result 2 — stop-on-touch costs return but buys real tail protection

| variant | win | avg | worst | verdict |
|---|---|---|---|---|
| 0.20 delta, no stop | 86% | **+$59.11** | −$427 | PASS |
| 0.20 delta, stop on touch | 74% | +$46.11 | **−$185** | PASS |

**This reverses the original finding.** Stopping on a touch costs ~$13/trade,
but it **more than halves the worst case** (−$427 → −$185) and still passes
OOS. On a book whose live pain came entirely from three fat tails, that is a
trade worth studying properly rather than dismissing — a lower average with a
much shorter tail may be the better risk-adjusted choice, especially before
real money.

Filed as an open question, not a change. It needs its own study with drawdown
and tail metrics, not just mean P&L.

## Result 3 — the actual culprit, and it was ours

Every live 7DTE closed via `time_stop`. **None reached expiry.** The live rule
was `CLOSE_DTE = 3`, and the comment recorded its provenance honestly:
`round(7 * 21/45)` — the 45DTE time-stop **scaled proportionally**.

| exit rule | n | win | avg | verdict |
|---|---|---|---|---|
| hold to expiry | 552 | 86% | **+$59.11** | PASS |
| **close at 1 DTE** | 552 | 80% | **+$39.43** | **PASS** |
| close at 2 DTE | 552 | 78% | +$25.31 | PASS |
| **close at 3 DTE** ← live | 552 | 71% | **+$11.16** | PASS |
| close at 4 DTE | 533 | 62% | −$0.42 | fail-OOS |

**The transplanted exit rule cost ~$28/trade** — it kept only 28% of what
holding one more day earns, and sits one step away from the OOS cliff at
4 DTE. It did not by itself make the trade a loser.

The reason is simple once seen: **theta is not linear in DTE — it accelerates
into expiry.** The last days of a 7-day option hold most of its decay. Closing
at 3 DTE surrenders the best part of the trade *after* having already borne
the risk of the first four days. You keep the danger and give away the pay.

Proportional scaling is a plausible-looking heuristic that is simply wrong for
a convex quantity. 21/45 of a 45DTE trade is not the same animal as 3/7 of a
7DTE trade.

## Change made

`CLOSE_DTE: 3 → 1`. Not 0: holding into expiry risks assignment on an ITM
short, and 1 DTE keeps most of the edge (+$39 of +$59) while still passing OOS.

`RULE_EPOCH = "2026-09-06"`. The 8 trades closed under `CLOSE_DTE=3` are a
**different strategy**, so `paper_record` counts them separately as `legacy`
and the promotion counter restarts. Pooling across a rule change is exactly
the error the audit flagged as E2 — we are not going to commit it deliberately
one week after naming it.

## The caveat that keeps this honest

All 8 live trades were entered between 07-30 and 08-13 — a single two-week
window containing one directional thrust. That is **not 8 independent
observations; it is one market event sampled 8 times.** The exit rule explains
the systematic bleed; the correlated window explains the size of the tail. Both
are true, and neither alone accounts for the record.

## The transferable lesson

**When a rule is carried from one timeframe to another, re-derive it — do not
scale it.** Theta is convex in DTE; proportional scaling of anything
time-related is invalid.

And the meta-lesson from the correction above: a modelling bug produced
confident, plausible, wrong numbers that I published. What caught it was not
review but **a second study whose result was obviously impossible** (a 45DTE
condor losing money at every exit). Cross-checking one study against another
is the cheapest bug detector available here, and it should be routine before
any finding is written up.
