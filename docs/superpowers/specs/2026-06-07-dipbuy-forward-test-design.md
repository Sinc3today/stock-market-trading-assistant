# Dip-Buy Forward Paper-Test — Design

**Date:** 2026-06-07
**Status:** Design — awaiting user spec review before implementation.
**Branch (anticipated):** `dipbuy-forward`

## Problem & Goal

The dip-buy study (`docs/DIPBUY_STUDY.md`) found a **promising but in-sample** edge:
oversold (RSI<30) dips bounce, and a bull-call debit spread captures it (+$135/trade,
68% win, IV-stress-robust, OOS-WF positive). It is **not yet validated** — the decisive
test is **forward paper-trading on unseen data**. This feature wires that forward test
into the live bot: from today on, when the oversold trigger fires, record a paper dip-buy
and let it accrue, so we confirm or kill the result on data the study never saw.

**Not** a real-money change. A 1-contract paper trade in a dedicated, headline-excluded
book. Promotion to real trading remains a separate, deliberate step (loop rule 13).

## Key Decisions

1. **New `candidate` book** — forward-tested promotion candidates, distinct from the
   `shadow` (extension-gate counterfactual) and `learning` (intraday sandbox) books.
   Excluded from the real-money-proxy `/trades` headline stats.
2. **Notifications folded into the 16:20 EOD exit digest**, clearly labeled — no separate
   phone push (it's research, not an actionable play).
3. **Isolation over DRY for the live path.** A new self-contained module
   `learning/dipbuy_forward.py` with its own entry + exit jobs. It does **NOT** modify the
   core `ExitManager` (the dip-buy's time-based hold doesn't fit its buckets, and the core
   exit loop is too important to overload). Both jobs are wrapped per Standing Rule #10.
4. **Reuse the validated study logic** for the trigger (`backtests.dipbuy_signal_study.
   rsi_series` + the fresh-cross predicate) and `backtests.realistic_pricing` for BS marks,
   so the live test uses the exact rule the study validated.

## Architecture

```
Daily (existing daily play job seam, SPY history + spot already loaded):
  dipbuy_forward.maybe_open_dipbuy(spy_df, ...)
    fresh RSI<30 cross today?  → build bull-call debit (~21 DTE) → record
    1-ct paper trade: book="candidate", source="auto-paper", strategy=
    "bull_debit_spread", dte_bucket="dipbuy", marker in notes.

Daily (new ~16:12 ET job, after the core exits):
  dipbuy_forward.resolve_candidates(polygon_client, ...)
    for each OPEN candidate trade:
      mark via BS off daily close + VIX
      close if  pnl >= 50% of max_profit   OR   trading-days-held >= 10
      log_exit(...) with the realized P&L.

16:20 EOD exit digest (existing job_exit_digest):
  extend to also include today's CANDIDATE closes, in a separate clearly
  labeled section ("Forward-test (candidate)"), still one push.
```

## Components

- **`learning/dipbuy_forward.py`** (new):
  - `is_fresh_oversold(spy_df) -> bool` — RSI(14)<30 today AND >=30 yesterday (reuses
    `rsi_series`).
  - `maybe_open_dipbuy(spy_df, spot, options_layer, recorder, today) -> dict | None` —
    on a fresh trigger, build the bull-call debit (bullish, ~21 DTE) via the same
    `OptionsLayer` the daily play uses, record a 1-ct `candidate`-book paper trade. Idempotent
    per day (no duplicate if one already opened today). Returns the trade or None.
  - `resolve_candidates(recorder, marker, *, spy_close, vix, today) -> list[dict]` —
    mark open candidate trades, close on 50%-target or 10-trading-days-held, return closed.
- **`config.py`**: `DIPBUY_FORWARD_ENABLED` (kill-switch, default True), `DIPBUY_FORWARD_DTE=21`,
  `DIPBUY_FORWARD_TARGET_PCT=0.50`, `DIPBUY_FORWARD_MAX_HOLD_TD=10`.
- **`journal/trade_recorder.py`**: `get_summary_stats()` already excludes `book != "shadow"`;
  change to exclude `book in ("shadow", "candidate")`. `get_open_trades`/lifecycle unchanged.
- **`learning/scheduler.py`** (or the daily play seam): register the entry hook + the
  ~16:12 resolver job (Mon-Fri, try/except), and extend `job_exit_digest` to append the
  candidate section.

## Data Flow & Exit Rule

- **Entry mark:** real structure via `OptionsLayer` (same as live bull plays), 1 contract.
- **Exit mark:** BS off the SPY daily close + VIX (the daily-swing marking the bot already
  uses elsewhere); `max_profit` from the recorded trade. Close when
  `pnl_dollars >= DIPBUY_FORWARD_TARGET_PCT * max_profit` OR
  `trading_days_held >= DIPBUY_FORWARD_MAX_HOLD_TD`. Trading-days-held counts scheduler
  invocations since entry (one per trading day), robust to weekends/holidays.
- **Why isolated marking is acceptable here:** the parity concerns from the intraday
  time-exit model don't apply — this is a *daily-swing* test marked on daily closes (the
  same granularity the 45DTE book already uses), not an intraday 5-min product.

## Error Handling

- Both jobs fully `try/except` (Standing Rule #10) — a forward-test failure never affects
  real plays or crashes the bot.
- `DIPBUY_FORWARD_ENABLED=False` disables both entry and exit (inert).
- Entry idempotency: skip if a candidate trade already opened today.
- Missing VIX/mark → skip that day's resolution for that trade (logged), retry next day;
  hard backstop closes at expiry via the existing expiry resolver if ever orphaned.

## Adoption

Accrue forward trades. After **≥10–15 candidate trades** on unseen data, review: if the
forward win-rate/expectancy tracks the study (~68% / +$135), promote via a separate
live-wiring spec; if it diverges, the candidate is falsified and shelved. Either way the
result is recorded in `STRATEGY_LOG.md` + KB. No auto-promotion.

## Testing (TDD)

- `is_fresh_oversold`: fires on a constructed fresh cross, not on a sustained sub-30 run.
- `maybe_open_dipbuy`: records exactly one `candidate`-book 1-ct bull_debit on a trigger
  day; no duplicate same-day; nothing on a non-trigger day; nothing when kill-switch off.
- `resolve_candidates`: closes at 50%-of-max-profit; closes at 10-trading-days-held; leaves
  a still-running trade open; ignores non-candidate books.
- `get_summary_stats` excludes `candidate` (extend the existing shadow-exclusion test).
- `job_exit_digest` includes a candidate section when candidate trades closed today.
- Offline only; deselect the flaky live-FRED tests.

## Out of Scope

- Real-money trading, sizing beyond 1 contract, live promotion (separate spec on success).
- Intraday management (daily-swing only).
- Trend-follow / transition-zone threads.
