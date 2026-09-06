# What structure fits 7DTE? — it was never the structure

**2026-09-06.** `backtests/seven_dte_structure_study.py`

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
| **0.20 delta** | 552 | 79% | **+$32.39** | **PASS** |
| 0.16 delta | 552 | 83% | +$28.64 | PASS |
| 0.12 delta | 552 | 89% | +$20.95 | PASS |
| 0.10 delta | 552 | 90% | +$14.73 | PASS |
| 0.08 delta | 552 | 91% | +$8.66 | fail-OOS |

Moving the shorts out raises the win rate all the way to 91% and **lowers the
money every step**. The credit surrendered costs more than the losses avoided.
The current 0.20 delta is the best of the tested set.

Wings confirm the existing choice too: $2 is too little credit (+$9.79), $10
carries a −$967 tail and fails OOS, **$5 is the sweet spot**.

## Result 2 — stop-on-touch is actively harmful

| variant | win | avg | verdict |
|---|---|---|---|
| 0.20 delta, no stop | 79% | +$32.39 | PASS |
| 0.20 delta, stop on touch | 57% | +$3.71 | fail-OOS |

Stopping when a short is touched converts recoverable positions into realised
losses — it costs **90% of the edge**. This is the third independent
confirmation of the same lesson (see `VELOCITY_GATE_STUDY`,
`INTRADAY_TOUCH`): adding reactive exits to this book destroys it.

## Result 3 — the actual culprit, and it was ours

Every live 7DTE closed via `time_stop`. **None reached expiry.** The live rule
was `CLOSE_DTE = 3`, and the comment recorded its provenance honestly:
`round(7 * 21/45)` — the 45DTE time-stop **scaled proportionally**.

| exit rule | n | win | avg | verdict |
|---|---|---|---|---|
| hold to expiry | 552 | 79% | **+$32.39** | PASS |
| **close at 1 DTE** | 552 | 74% | **+$22.24** | **PASS** |
| close at 2 DTE | 552 | 70% | +$9.90 | fail-OOS |
| **close at 3 DTE** ← live | 552 | 68% | **+$2.27** | **fail-OOS** |
| close at 4 DTE | 552 | 65% | −$2.44 | fail-OOS |

**The transplanted exit rule destroyed 93% of the edge** (+$32.39 → +$2.27)
and flipped the trade from PASS to fail-OOS.

The reason is simple once seen: **theta is not linear in DTE — it accelerates
into expiry.** The last days of a 7-day option hold most of its decay. Closing
at 3 DTE surrenders the best part of the trade *after* having already borne
the risk of the first four days. You keep the danger and give away the pay.

Proportional scaling is a plausible-looking heuristic that is simply wrong for
a convex quantity. 21/45 of a 45DTE trade is not the same animal as 3/7 of a
7DTE trade.

## Change made

`CLOSE_DTE: 3 → 1`. Not 0: holding into expiry risks assignment on an ITM
short, and 1 DTE keeps most of the edge (+$22 of +$32) while still passing OOS.

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

Three studies in a row have now found the same shape: **the structures are
fine, and our management of them is where the money leaks.** The velocity gate
was wrong, the touch-stop is wrong, the scaled time-stop was wrong. Every one
of them was an intervention that felt prudent and cost money.

When a rule is carried from one timeframe to another, **re-derive it, do not
scale it.**
