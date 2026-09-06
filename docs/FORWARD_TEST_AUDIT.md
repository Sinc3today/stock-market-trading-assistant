# Forward-test audit — what could be making our live results a lie

**Opened 2026-09-06.** The backtests have been through the falsification
gauntlet (OOS era-split, haircut, parameter sweep). The *live forward test* has
never had the same treatment. This document is the attempt to **disprove** the
forward-test record before we size real money against it.

Framing: assume the live numbers are wrong. For each assumption, the question is
not "is this reasonable?" but **"what evidence would prove this is broken, and
have we looked?"**

Sample under audit (as of 2026-09-06):

| | |
|---|---|
| Journal span | 2026-05-18 → 2026-09-03 (49 distinct entry days) |
| Records | 109 total — 83 closed, 26 open |
| Trustworthy closed | **45 (54.2%)** |
| Disciplined book (real-money proxy) | n=13 scored, 61.5% win, +$837 |
| Live book (actual broker fills) | n=3 scored, +$216 |

---

## Severity key

| | |
|---|---|
| **P1** | Could reverse the sign of the conclusion. Blocks sizing real money. |
| **P2** | Could materially shrink the edge (>25% of avg P&L). |
| **P3** | Real but bounded; note it and move on. |

---

## A. P&L computation

### A1 — Every closed trade's P&L was actually computed · **P1 · DISPROVED**
**Status: already falsified, 2026-09-06.** `TradeRecorder._calculate_pnl`
branches on `"debit_spread"`, but positions are recorded as
`put_debit_spread` / `call_debit_spread`. Those match no branch and fall
through to `return 0, 0`, filing as `"breakeven"` — then sit in the win-rate
denominator as non-wins.

- **Blast radius:** 32 of 77 non-void closed records.
- **Recomputed:** 8 wins / 23 losses / **−$718** of real P&L never recorded.
- **Already done:** `forward_scorecard` excludes them; a test pins
  `PNL_HANDLED_STRATEGIES` against the recorder's real branches.
- **Still to do:** repair the records (V-A1) so the history is usable, not
  merely excluded.

### A2 — Sign conventions are right per structure · **P1**
Credit structures (condor, credit spread, BWB) profit when exit < entry; debit
structures invert. A flipped sign turns a loss into a win of equal size.
- **Test:** for every scored closed trade, recompute P&L from the legs
  independently of `_calculate_pnl` and assert agreement within a cent.
- **Signature if broken:** a strategy whose win rate is suspiciously near
  100% or 0%, or |recomputed − recorded| ≈ 2×recorded.

### A3 — The ×100 contract multiplier is applied consistently · **P2**
Observed inconsistency: condors record `entry_value = -187.0` (dollars) while
debit spreads record `entry_value = 0.78` (per-share). If any *aggregate* reads
`entry_value`, it mixes units silently.
- **Test:** assert `|entry_value| ≈ entry_price × 100 × size` for every record;
  list violators. Then grep for readers of `entry_value`.
- **Signature if broken:** cost-basis / ROC figures off by 100×.

### A4 — Commissions are in the numbers · **P2 · known false, unquantified**
`EXIT_SLIPPAGE = 0.05/share` is applied on exits. **No commission is modeled
anywhere.** A 4-leg condor at ~$0.65/contract round trip ≈ **$5.20**.
- **Why it bites:** the *candidate* condor book averages **+$18.00/trade**. A
  $5.20 fee is **29% of that edge**. The disciplined book (+$85 avg) survives it.
- **Test:** re-run every book's aggregate with a per-leg commission and report
  which promotion bars stop clearing.

### A5 — Entry fills are achievable, not mid-price fantasy · **P2**
Backtests apply a 10% credit haircut. The forward test records the *modeled*
entry credit with no entry haircut.
- **Test:** compare the 3 `live` (real broker) fills against what the bot
  modeled for the same structure on the same day. n=3 is thin, but it is the
  only ground truth we own.

---

## B. Marking and pricing

### B1 — Exit prices reflect reality · **P1 · partially disproved**
18 records exited at exactly `$0.00` with `notes_exit` saying *"stop 75% of max
loss … fill=$0.00"*. **A stop at 75% of max loss cannot fill at zero** — the
price lookup failed and returned 0. Root cause is known: Polygon option
snapshots carry no bid/ask/last (see `reference_polygon_snapshot_no_quotes`).
- **Test:** flag every closed trade whose exit price is 0 while its notes claim
  a partial-loss stop, and every trade whose exit price is impossible given the
  structure (e.g. condor buy-back > wing width).
- **Note:** `forward_scorecard` already classifies these `SUSPECT_FILL`, but
  they currently land in `UNSCORED` first (strategy check runs earlier), so the
  count reads 0. The validator must classify independently of that ordering.

### B2 — Open positions are marked at a defensible price · **P1**
26 open positions carry `[MTM …]` notes containing the **SPY close**, not an
option mark. There is no live option pricing.
- **Test:** re-price every open position with the same model the backtest uses
  and report unrealized P&L. If the open book is deeply underwater, every
  closed-only headline is flattering.

### B3 — Daily-bar marking doesn't hide intraday breaches · **P3**
Already studied and shelved (`project_intraday_touch_shelved`,
`project_intraday_time_exit_inert`) — daily bars can't see a short strike
touched and recovered intraday. Carried here for completeness.

---

## C. Selection and survivorship

### C1 — Closed-only stats aren't survivorship-biased · **P1**
Every headline excludes open positions. Measured hold times: winners **13.1**
trading days, losers **4.7**. Stops firing fast is *intended*, but it means the
closed sample matures differently than the open one.
- **The acute case:** broken-wing butterfly shows **100% win** on
  **n=1 closed** while **17 sit open**. That headline is meaningless.
- **Test:** (a) mark all opens (B2) and recompute every book including
  unrealized; (b) per bucket, report closed/open ratio and refuse to display a
  win rate below a minimum closed count.

### C2 — Voided trades were voided for legitimate reasons · **P2**
6 records are voided. If any was voided *because it lost*, that is silent
cherry-picking.
- **Test:** print all 6 with their void reason and P&L-at-void; assert every
  reason is structural (synthetic stub, rh-sync thrash) and none is outcome-based.

### C3 — The bot trades every day that qualifies · **P2**
If entries silently fail on API errors, and errors correlate with volatile days,
the sample is quietly filtered toward calm days — which flatters a condor book.
- **Test:** cross-reference `spy_daily_plans.json` (days marked tradeable)
  against journal entries. Every tradeable day with no trade is an unexplained
  gap; bucket the gaps by VIX to see whether they cluster in stress.

### C4 — No duplicate records inflate n · **P3**
One duplicate signature found (2026-05-27, condor, entry 1.0, both unclosed).
- **Test:** assert no two records share (entry_date, strategy, ticker,
  entry_price, legs).

---

## D. Sample period and statistical power

### D1 — The sample covers more than one market state · **P1 · likely false**
The entire forward test is **2026-05-18 → 2026-09-03**: about 3.5 months of a
mostly-calm, upward-drifting tape (including the +5.7% August thrust). A
premium-selling book is *supposed* to look good here. **We have not survived a
regime change** — which is precisely the QQQ-condor promotion bar's wording.
- **Test:** bucket every scored trade by the regime on its entry day; report
  n per regime. Any regime with n<10 is a blind spot, and any regime with n=0
  must be named as untested.

### D2 — n is large enough to mean anything · **P1**
Disciplined book n=13 at 61.5% win. The 95% confidence interval on 8/13 is
roughly **35%–83%** — it does not exclude a coin flip, let alone confirm the
backtest's 74%.
- **Test:** compute Wilson intervals for every headline win rate and display
  them. Also: the probability of observing ≥8/13 wins if the true rate were 50%.
- **Expected outcome:** most current claims will be shown to be *not yet
  distinguishable from noise*. That is the honest answer, and the dashboard
  should say it.

---

## E. Book hygiene

### E1 — Books aren't contaminated by each other · **P2**
`disciplined` is the real-money proxy; `learning` is the acknowledged no-edge
sandbox. If learning-book outcomes reach threshold tuning, we are optimizing on
noise (the existing audit note T3#13 guards `PredictionLog.accuracy`, but the
trade journal has no equivalent guard).
- **Test:** assert no aggregate feeding the hypothesis engine mixes books.

### E2 — The disciplined book is internally consistent over time · **P2**
The **IVR veto was removed on ~2026-08-XX**, changing which days trade. Trades
before and after are governed by different rules but pooled in one number.
- **Test:** split the disciplined book at each known strategy change and report
  sub-samples. If pre- and post-change results differ materially, the pooled
  number describes a strategy that no longer exists.

---

## F. Prediction scoring

### F1 — "Correct" means something · **P2**
Direction calls resolve to correct/wrong/push. If the push band is narrow, a
+0.05% day counts as a directional win, inflating accuracy toward a coin flip's
natural 50%.
- **Test:** re-score every prediction at push bands of 0.0 / 0.25 / 0.5 / 1.0%
  and report how accuracy decays. A claim that survives only at band=0 is noise.

### F2 — Prediction horizon matches the trade horizon · **P3**
Predictions resolve on the **same-day close**; the plays they accompany are
7–45 DTE. A 1-day directional call is weak evidence about a 45-day structure.
- **Test:** none needed — this is a framing correction. State on the dashboard
  that direction accuracy describes the *daily call*, not the book's edge.

---

## G. Execution order

1. **V-A1** repair the 32 unscored records (backup first) — unblocks everything.
2. **V-B2 / V-C1** mark the opens; recompute all books including unrealized.
3. **V-D1 / V-D2** regime coverage + Wilson intervals — the "do we know anything
   yet?" answer.
4. **V-A4** commission sensitivity — which promotion bars survive fees.
5. **V-A2 / V-A3 / V-B1 / V-C2 / V-C3 / V-C4 / V-E1 / V-E2 / V-F1** the rest.

Each validator **reports a verdict with evidence** and is written to *fail
loudly*, not to reassure. A validator that cannot fail is not a validator.

---

---

## RESULTS — first full run, 2026-09-06

`.venv/bin/python -m backtests.forward_audit` → **7 FAIL · 3 WARN · 3 PASS**
(from 9/2/2 before the fixes below landed).

### Fixed during this pass

| | |
|---|---|
| **A2 · PASS** | Three modules each kept a private credit-vs-debit list and they disagreed. `broken_wing` SIGN-FLIPPED in `exit_manager`, and its close cost was clamped at zero (phantom max-loss on a structure that can legitimately pay you to close). Now one source of truth: `trade_recorder._pnl_convention`. |
| **A1 · improved** | `_calculate_pnl` no longer ends in `return 0, 0`. Unknown structures return `None` and `log_exit` records `outcome="unscored"`. Variant names (`put_debit_spread`) now resolve by suffix. Remaining 14 are legacy records awaiting repair. |
| **C2 · PASS** | Was a false positive in the validator itself — it matched the void reason against a 70-char *display* slice, truncating "thrash". All 6 voids are structural. |

**The BWB fix moved a headline number:** the open tail re-marked from
**−$2,482 to +$533**. The earlier deficit was an artifact of the clamp, not a
real loss. This is the audit working — and a reminder that a scary number from
a broken instrument is still a broken number.

### Still failing — these gate real money

| ID | Finding |
|---|---|
| **A1** | 14 legacy records still unscored (+$315 hidden). Repair pending. |
| **B1** | 18 stop-exits recorded an impossible `$0.00` fill. |
| **C1** | Claims resting on <10 closed trades: 7DTE (n=8), QQQ (n=6), **BWB (n=1 closed, 17 open)**, dip-buy (n=3). |
| **D1** | `choppy_high_vol` and `trending_high_vol` **never traded live (n=0)**. `choppy_transition` (6), `trending_up_calm` (7), `event_day` (1) are thin. 12 trades have no plan record. |
| **D2** | **Disciplined 8/13 = 61.5%, CI [35.5, 82.3], p=0.291 — not distinguishable from a coin flip.** Only `candidate` beats chance (15/18, p=0.004). |
| **E2** | Only **1** closed trade exists under the post-IVR-veto ruleset. The headline describes a strategy we no longer run. |
| **A3** | 51 records store `entry_value` per-share instead of dollars. |

### Passing

- **F1** — direction accuracy *rises* as the push band widens (61% → 69% at
  ±0.5%), so it is not riding noise. The lone clean result.
- **A4 (WARN)** — no book flips sign under $0.65/leg commissions, but the
  **7DTE condor** was already negative and goes to −$59/trade. Fees are still
  unmodeled in the live path.

### What the dashboard now shows because of this

- 95% Wilson interval next to every win rate, and the words *"not
  distinguishable from a coin flip"* where the interval spans 50%.
- The open tail's model mark, or an explicit *"unmarked — unknown"* rather
  than an implied $0.
- A regime-coverage table naming what has **never** been traded live.

### Next actions

1. **V-A1 repair** the 14 legacy records (backup `trades.json` first).
2. **B1**: stop writing a `$0.00` fill when the price lookup fails — record the
   exit as unscored instead, so a failure never masquerades as a trade.
3. **A3**: normalise `entry_value` to dollars; audit readers first.
4. Keep accumulating. D1/D2/E2 are not bugs — they are *"the sample is too
   young and too calm"*, and only time and a regime change fix them.

---

## Standing rule this establishes

> A forward-test number is not publishable until (a) every record in it was
> actually scored, (b) the open tail is marked, (c) the sample's regime coverage
> is stated, and (d) the confidence interval is shown next to the point estimate.

The `/scorecard` page enforces (a) already. This audit adds (b)–(d).
