# Intraday Time-Exit Model — Design

**Date:** 2026-06-05
**Status:** Design approved (sections 1-4); ready for user spec review → implementation plan.
**Branch (anticipated):** `intraday-time-exit-model`

## Problem & Goal

The 2026-06-03 loss-attribution diagnostic showed **put_debit_spread 0DTE loses on
exit timing, not direction**: of 47 distinct OOS trades (2024-25), 30 (64%) drifted
to EOD at a mean −$102.70 each, and the 17 that hit target (+$97) can't cover them.
The direction read is fine — every trade fired on a down/sideways trend. The killer
is **holding to the close on non-follow-through days**.

The current intraday backtest already walks **real 5-min option bars** and models
`target`/`stop`/`eod` exits — it does NOT lack bar resolution. What it lacks is a
**time-based exit rule**. The live `ExitManager` has a deeper gap: it marks via
Black-Scholes off the SPY **daily close** + VIX (not intraday), and although it
computes a `forced_close_time` per rule, `_evaluate()` never enforces it.

**Goal:** add time-based exit rules (hard-close + scratch), prove or disprove them
out-of-sample via the existing walk-forward, wire any winner into live trading with
backtest/live parity, and — critically — make the bot able to **learn whether an
exit was a mistake** (did we cut a winner, or save a loser?). See
[[feedback_walk_forward_honest_ml]], [[feedback_falsification_anti_bias]],
[[project_intraday_touch_shelved]].

## Key Decisions

1. **One shared, stateless exit evaluator** (`evaluate_intraday_exit`) used by BOTH
   the backtest loop and the live `ExitManager` → parity by construction (the WF
   validates the exact function live runs). Mirrors the Phase 4b pricer seam.
2. **Two candidate rules:** hard-close@T (close unconditionally at fixed ET time)
   and scratch@T(θ) (close at T only if `pnl < θ·max_profit`). Evaluated AFTER the
   existing profit-target/stop so a working trade still takes its win.
3. **Exit-counterfactual / exit-quality (piece D)** is a first-class component: on
   every exit, record P&L_hold (hold-to-EOD/expiry) alongside P&L_exit and score
   `exit_quality = P&L_exit − P&L_hold`. Backtest gets P&L_hold free (it has the
   forward path); live fills it from the existing outcome/expiry resolvers. Feeds
   `exit_timing` KB entries the reflector already consumes.
4. **WF-gated adoption + kill-switch:** a `(strategy,dte_bucket)` gets a time-exit
   live ONLY if its arm passes the existing WF verdict thresholds; a global
   `config.INTRADAY_TIME_EXIT_ENABLED` kill-switch force-disables without redeploy.
5. **Anti-curve-fit protocol** (47-trade sample): coarse 3-time grid (not per-bar),
   robustness across windows over peak aggregate, one shared parameter by default,
   OOS pass-rate is the verdict, sample-honesty flagging under ~30 distinct trades,
   and the exit_quality counterfactual as a sanity guard.
6. **"WF finds nothing" ships inert:** if no arm passes, B ships with no params
   populated (live unchanged) and A+D still deliver measurement + exit learning.
   Disproving the time-stop is a valid outcome.

## Architecture

Approach: **shared stateless evaluator + pricer seam** (chosen over backtest-first-
duplicate-later, which invites backtest/live drift; and over a full event-driven
replay engine, which is YAGNI for two rules). Four pieces:

```
signals/intraday_exit_rules.py   ← shared core
  evaluate_intraday_exit(position, mark, now_et, rule) → ExitDecision | None
      ExitDecision = {exit_price, reason, fired_at}

   (A) BACKTEST                                   (B) LIVE
   simulate_0dte_day /                            ExitManager._evaluate calls the
   _simulate_short_dte_with_expiration            SAME evaluate_intraday_exit
   call the evaluator in their 5-min loop         (fixes the latent
   via HistoricalPricer (real option aggs)        forced_close_time-unenforced bug)
        │                                                │
        │                                          (C) LIVE PRICER
        │                                          BS off intraday 5-min SPY spot
        │                                          + VIX (Starter plan has no
        │                                          intraday option quotes)
        └──────────────────┬─────────────────────────────┘
                           │
            (D) EXIT-COUNTERFACTUAL / EXIT-QUALITY
   record P&L_hold beside P&L_exit; exit_quality = P&L_exit − P&L_hold;
   aggregate per (strategy, dte_bucket, exit_reason) → exit_timing KB.
```

| Piece | What | Risk | Gate |
|---|---|---|---|
| A | Time-exit rules in shared evaluator; backtest calls it | none | — |
| B | Live ExitManager calls same evaluator; enforces time-exits | live | WF pass + kill-switch |
| C | Live intraday mark (BS-off-spot) + parity check | data-limited | part of B |
| D | Exit-counterfactual + exit_quality scoring | none | — |

## Exit Rules

Evaluation order inside the existing 5-min loop:
`profit-target → hard-stop (where configured) → scratch@T → hard-close@T → EOD/expiry`.

- **hard-close@T** — at first bar with `now_et ≥ T`, close unconditionally at the
  current mark. Param: `hard_close_time`.
- **scratch@T(θ)** — at first bar with `now_et ≥ T`, close only if
  `pnl < θ·max_profit`. Winners past θ keep riding. Params: `scratch_time`,
  `scratch_theta`.

## Walk-Forward Integration

- Backtest runs the network-heavy sim ONCE, recording each trade's full 5-min mark
  path (→ `logs/wf_trade_paths.jsonl`, sibling to the existing `wf_trade_rows.jsonl`).
- A pure offline arm-replay layer applies every arm — `baseline`,
  `hard-close@{12:00,13:00,14:00}`, `scratch@{12:00,13:00,14:00}×θ{0,10%}` — to the
  SAME paths (paired comparison, low variance), feeding each through the existing
  `window_stats → window_verdict → aggregate_verdict` (thresholds calibrated
  2026-06-02). Output: per-arm × per-`(strategy,dte_bucket)` pass/fail table.
- The winning arm is the one passing the MOST OOS windows, not the highest aggregate.

## Live Marking & Parity (C)

- Live `ExitManager` marks via **BS off the intraday 5-min SPY spot + VIX** (the spot
  IS available on the stocks plan; an improvement over today's daily-close BS).
- **Parity gate:** the backtest computes BOTH marks at every step — the real
  option-bar mark (truth) and the BS-off-spot mark (what live sees) — and reports how
  often they yield DIFFERENT exit decisions. B ships for a combo ONLY if its arm
  passes the WF on real marks AND the BS-off-spot mark reproduces those exits within
  a concrete tolerance: **exit decisions agree on ≥90% of the combo's trades** (same
  exit bar ±1) **AND the per-trade mean P&L gap between the real-mark and BS-mark arm
  is < $10**. The 90% / $10 defaults live in `config` so they're adjustable. Delayed
  intraday option aggs, if available, are a later upgrade.
- Live mark-fetch failure → fall back to today's daily-close BS (safe-degraded,
  logged).

## Data Flow

```
Backtest: sim (real 5-min option bars) → per-trade path + both marks
        → arm-replay (offline) → per-arm WF verdicts + parity-divergence report
Live (every 5 min, 9:00-15:55 ET): ExitManager → BS-off-intraday-spot mark
        → evaluate_intraday_exit (kill-switch + WF-earned params) → log_exit
        → counterfactual intent recorded → outcome/expiry resolver fills P&L_hold
        → exit_quality scored → exit_timing KB entry
```

## Error Handling

- All live exit work wrapped per Standing Rule #10 (one failure never crashes the bot).
- Kill-switch off → evaluator skips time-rules entirely (live = today's behavior).
- Live mark fetch fail → daily-close BS fallback (logged).
- WF arm-replay tolerates missing paths (drop that trade, logged — no silent
  truncation).
- WF finds no passing arm → B inert, params unpopulated, live unchanged.

## Testing (TDD)

- `evaluate_intraday_exit` is pure → unit-test each rule + eval order: hard-close
  fires at T; scratch fires only when `pnl < θ·max_profit`; a working trade takes
  profit-target first; the EOD/expiry backstop is unchanged when no time-rule set.
- Arm-replay tested on synthetic paths (a path that hits target pre-T ignores the
  time-rule; a dead path exits at scratch@T; a drifting path exits at hard-close@T).
- `exit_quality` sign convention tested both ways (early cut of a winner → negative;
  saved loser → positive).
- Parity-divergence tested on a fixture where the two marks disagree.
- Backtest/live parity: same fixture through `evaluate_intraday_exit` from both the
  sim path and the ExitManager path → identical decision.
- Live ExitManager: BS-off-intraday-spot mark used when intraday spot present;
  daily-close fallback when absent; kill-switch disables time-rules.

## Scope & Sequencing

Single spec, but built+verified in order A → D → (run WF) → B/C, so live changes
(B/C) are parameterized by the WF result. A and D carry no live risk and are always
delivered; B/C ship only what the WF + parity gate earn.

## Follow-ups (noted, not in this spec)

- Delayed intraday option aggregates for a truer live mark (verify Starter plan).
- Generalize time-exit learning to 1-3DTE / 45DTE once 0DTE proves the mechanism.
- Surface exit_quality in the falsificationist reflector's daily KB rollup.
- Re-open the iron_condor 1-3DTE thread once it clears the attribution floor.
