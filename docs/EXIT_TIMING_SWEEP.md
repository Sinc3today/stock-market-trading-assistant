# Exit-timing sweep — which buckets run a scaled rule?

**2026-09-06.** `backtests/exit_timing_sweep.py`

After the 7DTE time-stop turned out to be `round(7 * 21/45)` — the 45DTE rule
scaled proportionally — the obvious question was whether anything else carries
the same defect. `TIME_EXIT_FRAC = 21/45` still governs the broken-wing
forward test, and **17 BWB positions are open right now**, so this was not
academic.

Calm regime, 2018+, OOS era-split, 10% credit haircut, commissions, and the
live "70% of max profit **or** the time stop" rule.

## The bug this sweep found first — in itself

The first run reported the 45DTE condor losing money at **every** exit point.
That is impossible: it is the core edge, 70% win live. The cause was a
**calendar-vs-trading-day** error — `DTE` is calendar days, the price index is
trading days, and walking `DTE - close_dte` *rows* forward overshoots ~40%.

The same bug was in `seven_dte_structure_study.py`, and it had already produced
published numbers. Those are corrected in `SEVEN_DTE_STRUCTURE_STUDY.md`. Two
of its conclusions reversed outright.

**A study whose result is obviously impossible is the cheapest bug detector we
have.** It should be routine to cross-check one against another before writing
anything up.

## Results

### Condor 45DTE — configured 21 · **flagged, not changed**

| close at | win | avg | verdict |
|---|---|---|---|
| **7** | 67% | **+$12.78** | **PASS** |
| 14 | 65% | +$3.38 | fail-OOS |
| **21** ← live | 66% | **−$2.78** | **fail-OOS** |
| 28 | 62% | −$7.44 | fail-OOS |

The core strategy's configured rule is **marginal-to-negative after
commissions**, and holding to 7 DTE tests far better. This is consistent with
the live record — disciplined condors are **−$88 since July 1** — and with
audit A4, where fees eat 7% of the edge.

**Deliberately not changed.** 21 DTE is the industry-standard gamma-risk rule,
and this model prices from daily closes with no path-dependent tail
measurement, so it will *understate* the pain of holding through expiry week.
Changing the core strategy on one simplified sweep is exactly the error the
validation agenda exists to prevent. Filed as the highest-priority open study:
it needs drawdown and tail metrics, not mean P&L alone.

### Condor 7DTE — configured 1 (was 3) · **fixed**

| close at | win | avg | verdict |
|---|---|---|---|
| 0 | 87% | +$55.46 | PASS |
| **1** ← now | 80% | **+$38.52** | PASS |
| 3 ← was | 71% | +$11.16 | PASS |
| 4 | 62% | −$0.42 | fail-OOS |

### BWB 45DTE — configured 21 · **minor leak, left alone**

| close at | win | avg | verdict |
|---|---|---|---|
| 14 | 73% | **+$12.84** | PASS |
| 7 | 74% | +$12.35 | PASS |
| **21** ← live | 69% | +$9.57 | PASS |

Configured rule passes. Closing at 14 is +$3.27/trade better — real but small,
and 45DTE is the *native* rung for `21/45`, so this is not the scaling bug.
Not worth churning a running forward test for $3.

### BWB 30DTE — configured 14 (scaled) · **the real finding, and it is worse**

| close at | win | avg | 2018-22 | 2023+ | verdict |
|---|---|---|---|---|---|
| 10 | 74% | +$14.79 | −$0.29 | +$29.10 | fail-OOS |
| **14** ← live | 72% | +$8.15 | −$1.05 | +$16.88 | fail-OOS |
| 21 | 59% | −$3.74 | −$5.15 | −$2.39 | fail-OOS |

**Every** exit point fails OOS. The old era is negative at all of them while
the new era is strongly positive — the classic signature of a structure that
works in *this* market and did not work before it.

So BWB-30DTE's problem is **not** the scaled time-stop. Retuning the exit
cannot rescue it, because there is no setting at which it survives the era
split. The 30DTE rung looks profitable only because the sample is recent.

BWB-45DTE, by contrast, passes at every reasonable exit. The original
`BROKEN_WING_STUDY` validated **30 and 45DTE**; this sweep says only 45
survives with commissions and the full OOS split.

## Actions

1. **7DTE:** `CLOSE_DTE 3 → 1`. Done.
2. **BWB-30DTE:** raise at the next review — the honest options are to drop the
   30DTE rung and run 45 only, or to keep it running with the promotion bar
   annotated *"backtest fails OOS at every exit; live record is the only
   evidence."* It currently has **8 open positions and 1 closed**, so nothing
   is decided by it either way yet.
3. **Condor 45DTE:** open study, highest priority. Do **not** move 21 DTE on
   this evidence.
4. **BWB-45DTE:** leave at 21.

## The rule, restated

Re-derive a time rule per rung; never scale it. And when a sweep contradicts
something already established, **suspect the sweep first** — that instinct is
what caught the calendar bug here.
