# Validation agenda — the recurring exercise, and the gates it runs at

**Purpose.** Turn "test, validate, disprove, plan" from a thing we did once in
September 2026 into a thing that happens on a schedule and at every gate. The
2026-09-06 audit found that **46% of the live record was fabricated** — nobody
was lying, the instrumentation was. That failure mode is silent by nature, so
it needs a standing process, not vigilance.

Companion docs: `FORWARD_TEST_AUDIT.md` (the assumption register),
`STRATEGY_LOG.md` (why thresholds are what they are).

---

## Part 1 — The exercise

Run in this order. **The order matters**: never analyse a number before you
have established you are allowed to believe it.

### Step 0 — Trust the instrument before the result
```
.venv/bin/python -m backtests.forward_audit
```
Any **P1 FAIL** stops the exercise. Fix the instrument, then restart at Step 0.

> The one rule that would have caught the September bug: *a metric that has
> never been reconciled against an independent calculation is a rumour.*

### Step 1 — State the claim as something falsifiable
Write the claim as a sentence that could be **wrong**. "The condor has an
edge" is not a claim; "the disciplined condor book wins >65% with avg >$40
across at least two regimes" is.

### Step 2 — Attack it before defending it
For each claim, ask the four questions that have actually caught things here:

1. **Is the number computed the way I think?** (A1: it was not.)
2. **Does an independent implementation agree?** (A2: it did not.)
3. **What is excluded from it, and is the exclusion outcome-correlated?**
   (C1/C2: open positions, voids.)
4. **Would this number look the same if the strategy had no edge?** (D1/D2:
   a premium seller looks great in any calm drift.)

### Step 3 — Size the sample honestly
Point estimates are banned in reporting. Every win rate is quoted with its
Wilson interval. If the interval spans 50%, the claim is **"not yet
measurable"** — not "promising".

### Step 4 — Write the verdict down, including the disproofs
Findings go in `docs/`, one file per study, stating what was rejected and why.
A rejected idea is a durable asset; an unrecorded rejection gets re-proposed.

### Step 5 — Update the register and the agenda
New assumptions discovered → add to `FORWARD_TEST_AUDIT.md` with a
falsification test. New recurring risk → add a validator to
`backtests/forward_audit.py`. **A finding without a validator will recur.**

---

## Part 2 — The gates

Each gate has entry criteria (what must be true to start) and exit criteria
(what must be true to pass). **Gates are not skippable and not reorderable.**

### Gate 0 — Instrumentation trust · **RE-SCOPED 2026-09-09**
*Can we believe our own journal?*

| | |
|---|---|
| Exit criteria | **Zero P1 FAIL among the Gate-0 validators** (A1–A4, B1–B3, C2, C4, F1); every headline carries a CI |
| Status | **8 PASS / 2 FAIL** — see below |
| Remaining | **B2** (P1) and **A3** (P2) |

> **Why the criterion changed.** It used to read "zero P1 FAIL" across the whole
> audit. But the audit spans every gate: C1 asks whether the *sample* has
> matured, D1 whether it covers more than one market state, D2 whether the
> result beats chance. Those are Gate 1 and Gate 2 questions, closed only by
> time and a regime change.
>
> So Gate 0 required, as a precondition for *starting to collect trades*, that
> we had already collected enough trades. It read IN PROGRESS for weeks while
> every engineering defect under it was being closed.
>
> Scoping now lives in `backtests/forward_audit.GATE_OF`, not in this
> paragraph, and `tests/test_gate_scope.py` fails if a validator has no gate or
> if a sample-maturity check is assigned to Gate 0. Ask for the per-gate
> picture with `forward_audit.gate_status()`.

**The two open items:**

- **B2 — open tail marked by model only (P1).** Previously logged as an accepted
  risk, and it is: this Polygon tier returns no option bid/ask, so the open tail
  is marked from a Black-Scholes model rather than quotes. It flips between WARN
  and FAIL with the market — WARN at +$463 unrealized on 2026-09-08, FAIL at
  −$393 the next morning — because a negative tail means the closed-only
  headline flatters us. The instrument limitation is accepted and disclosed on
  `/scorecard`; what is NOT settled is how far the model marks sit from real
  fills. That is the open question, and only real fills answer it.
- **A3 — one record stores `entry_value` inconsistently (P2).** `9C475659`, the
  live RH-synced position expiring 2026-09-18, has `entry_price=1.6`, `size=3`
  and `entry_value=-640`, where `1.6 × 100 × 3 = 480`. rh_sync updates size and
  price on re-sync but never recomputes the derived field. **Nothing in
  production reads `entry_value`** — it is written by `trade_recorder`, repaired
  by `journal_repair`, validated by A3, and consumed by no decision anywhere.
  Two options, both defensible, neither taken yet: recompute it on update, or
  delete the field and the two modules that maintain it. The disagreement still
  matters as a *signal* — it says the recorded size of a real-money position
  changed and the record only partly absorbed it.

### Gate 1 — Candidate promotion
*Has any structure earned a place in the disciplined book?*

| | |
|---|---|
| Entry | Gate 0 clean (its OWN validators — see the re-scope note) |
| Exit | ≥15 closed, ≥70% win **with CI lower bound >50%**, avg >$20 **net of commissions**, no loss > max_loss, and ≥2 distinct regimes |
| Status | 7DTE 8/15 (avg −$54, failing), QQQ 6/15, BWB 1/15, dip-buy 3/15 |

> Note the two additions to the original bar: **CI lower bound** and **net of
> commissions**. A4 showed the 7DTE condor's avg goes to −$59 under $0.65/leg.

### Gate 2 — Disciplined edge confirmation
*Is the core edge real out of sample, in live conditions?*

| | |
|---|---|
| Entry | Gate 1 passed by ≥1 structure |
| Exit | ≥30 closed disciplined trades, CI lower bound **>55%**, positive in **each** of ≥3 regimes, and survives a **regime change** |
| Status | n=21, CI [41, 79] — **spans 50%**. `choppy_high_vol` and `trending_high_vol` still n=0 |

### Gate 3 — Sandbox auto-execution (Tradier)
*Can the machine place what the model intends, without money at risk?*

| | |
|---|---|
| Entry | Gate 2 passed; `TRADIER_SANDBOX_TOKEN` present |
| Exit | ≥20 sandbox multileg orders with **preview-parity** (mapped order == intended structure, 100%); all guardrails proven by test: `TRADIER_LIVE=False` default, entry-window, pacing, concentration, kill-switch |
| Blocker | Sandbox token not yet provided. Step 1 (`brokers/order_mapper.py`) is built and tested |

### Gate 4 — Live small money
*Does the edge survive real fills?*

| | |
|---|---|
| Entry | Gate 3 clean for ≥2 weeks |
| Exit | ≥20 real fills; realised win rate within the **CI** of the paper rate; slippage vs model quantified and folded into the backtest haircut |
| Caps | 1 lot, per-trade max loss, daily loss limit, ~10% drawdown kill-switch |
| Status | 3 real closed fills. Far from the bar |

### Gate 5 — Scale
| | |
|---|---|
| Entry | Gate 4 passed |
| Exit | Position sizing derived from measured drawdown, not intuition; re-run the whole exercise at the new size |

---

## Part 3 — Cadence

| When | What |
|---|---|
| **Weekly** (Sun) | `forward_audit`; log any new FAIL |
| **Monthly** | Full exercise Steps 1–5 on the live claims; refresh CIs and regime coverage |
| **At every gate** | The whole exercise, plus the gate's own exit criteria |
| **After any regime change** | Immediately — this is the sample we are missing, so it is the most informative moment we get |
| **After any P&L / pricing / marking change** | Steps 0–2 before trusting a single downstream number |

---

## Part 4 — Standing backlog

Ordered by what unblocks the most. Status as of **2026-09-09**.

| # | Item | Gate | Sev | Status |
|---|---|---|---|---|
| 1 | A1 unscored records | 0 | P1 | **DONE** — engine fixed, 13 rescored (+$315), 19 quarantined |
| 2 | A2 sign-convention drift | 0 | P1 | **DONE** — one source of truth, BWB flip closed |
| 3 | B1 phantom $0.00 fills | 0 | P1 | **DONE** — root cause was 0DTE intrinsic marking, not lookup failure |
| 4 | A3 `entry_value` unit mismatch | 0 | P2 | **DONE** — third instance of exact-name matching; 51 records rescaled |
| 5 | A4 commissions unmodeled | 1 | P2 | **DONE** — `pnl_net` recorded per trade; promotion bars judged net |
| 6 | C4 duplicate entry signature | 0 | P3 | **DONE 2026-09-09** — not duplicates. Signature omitted expiry + DTE bucket, so a 0DTE and a 1-3DTE stub opened in the same minute collided; both were void anyway. Signature widened, void records skipped. |
| 7 | B2 open tail marked by model only | 0 | P1 | **OPEN** — limitation accepted and disclosed, but the size of the model-vs-fill error is unmeasured. Needs real fills. |
| 8 | C1 thin candidate buckets | 1 | P1 | **TIME** — needs closed trades |
| 9 | D1 untested regimes | 2 | P1 | **TIME** — needs a regime change; consider deliberately paper-trading the missing states |
| 10 | D2 CI spans 50% | 2 | P1 | **TIME** — needs n |
| 11 | E2 only 1 trade under current ruleset | 2 | P2 | **TIME** — accumulating since 2026-08-14 |
| 12 | F2 prediction horizon ≠ trade horizon | — | P3 | **FRAMING** — state it on the dashboard, don't "fix" it |

| 13 | `entry_value` is written, repaired and validated but never read | 0 | P2 | **DECISION NEEDED** — recompute on rh_sync update, or delete the field and its two maintainers |
| 14 | strategy names that cannot be classified (`custom`/`none`) logged at ERROR | 0 | P3 | **DONE 2026-09-09** — `convention_status()` splits "unclassifiable by design" from "unknown name". Same refusal to score; ERROR now means something again. |
| 15 | a crashing validator was reported as a WARN under its function name | 0 | P1 | **DONE 2026-09-09** — it dropped out of gate accounting entirely, so a gate could read clean with a dead validator inside it. Now a P1 FAIL that keeps its id. D1 was crashing on an empty journal. |

**Items 8–11 are not bugs.** They are "the sample is too young and too calm."
No amount of engineering closes them; only time and a regime change do. The
honest move is to keep them visible and refuse to promote past them.

---

## The rule this whole document exists to enforce

> **We do not get confident by finding more evidence that we are right. We get
> confident by failing to prove that we are wrong — with instruments we have
> verified, on samples large enough to speak, across conditions we have not yet
> seen.**
