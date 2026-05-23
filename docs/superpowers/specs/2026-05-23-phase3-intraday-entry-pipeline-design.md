# Phase 3 — Intraday Entry Pipeline — Design Spec

**Date:** 2026-05-23
**Status:** Approved design, pending implementation plan
**Author:** brainstormed with Claude Code

---

## Problem / Motivation

`scanners/intraday_scanner.py` already runs every 5 min during market hours and emits structured `SPYSetup` candidates with conviction tier + score via `SPYOptionsEngine`. Today, those setups are alert-only — they fire to Pushover/Discord but never become paper positions. This is the gap. Phase 3 wires the existing intraday signal stream into `paper_broker.execute_signal` (built in Phase 2b, currently no caller) so high-conviction intraday setups become disciplined-book paper trades.

This is **the first phase that changes the bot's live behavior** — every prior phase was "no live change" or "parity-validated identical behavior." Phase 3 is the moment the bot starts taking intraday positions.

## Goals

- Connect `intraday_scanner` → `paper_broker.execute_signal` for high-conviction setups.
- Apply entry-tier filter, DTE assignment, and dedup rules per the locked decisions below.
- Ship with an emergency-kill flag (`INTRADAY_PAPER_BROKER_ENABLED`, default `True`) so live behavior changes on merge but can be flipped off in one config commit if needed.
- Preserve everything from Phases 1, 2a, 2b — no regression to the 09:15 daily play, no change to the 16:08 exit cron's 45DTE behavior, no change to the alert path.

## Non-Goals (deferred)

- **Learning book** — Phase 3 ships single-book (disciplined) only. The dual-book design (with exit-feasibility as the discriminator) is deferred to Phase 4, when we'll have real intraday paper-trade data to inform the exit-feasibility predicate design.
- **Per-strategy exit-feasibility rules** — explicitly out. Phase 4 design pass.
- **1-3DTE-only paths and the day-of-week magic-cutoff variants** — H2's time-of-day rule covers it; finer day-of-week tuning waits for real data.
- **Event-bus refactor of `intraday_scanner`** — inline wiring is fine for one consumer. Refactor when a second consumer (Phase 4 learning book) needs to subscribe.
- **Notification routing for intraday paper trades** — for v1, intraday entries are silent in Pushover/Discord (the existing intraday `notifier.alert` still fires for the alert side; the new `execute_signal` writes to the journal but doesn't post a separate message). Phase 4 will revisit.

## Locked Decisions

| Decision | Choice | Source |
|---|---|---|
| Book strategy | Single (disciplined). Learning book → Phase 4 | User chose "Single book in Phase 3" |
| Entry tier | `conviction == "high"` (score ≥ `SCORE_HIGH_CONVICTION` = 68); configurable via `ENTRY_TIER_MINIMUM = "high"` | User chose "High only for now, configurable later" |
| Position dedup | **Option D**: one open at a time per `(strategy, dte_bucket)`, plus a per-day cap of 2 entries per `(strategy, dte_bucket)` combo. After a close, a fresh setup can re-open up to the per-day cap. | User chose "D" after scenario walkthrough |
| DTE assignment | **H2 hybrid**: morning (before `INTRADAY_DTE_MORNING_CUTOFF` = 12:30 ET) → 0DTE; afternoon → 1-3DTE. Friday PM safeguard: Friday afternoon defaults back to 0DTE (no weekend exposure). Ultra-conviction exception: score ≥ `ULTRA_CONVICTION_DOUBLE_DTE_SCORE` = 85 opens **both** 0DTE and 1-3DTE (rare, ~1-2/week). | User chose "H2" after scenario walkthrough |
| Cross-strategy cap | Existing `MAX_CONCURRENT_DISCIPLINED = 3` from Phase 2b. Across all sub-strategies, never more than 3 disciplined-book positions open simultaneously. | Phase 2b |
| Feature flag | `INTRADAY_PAPER_BROKER_ENABLED = True` (default ON). Acts as emergency kill-switch — flipping to `False` and committing instantly disables the pipeline without untangling wiring or reverting commits. | User explicit: "everything ready by Tuesday, don't want extra step" |
| Wiring location | Inline in `intraday_scanner.run()` — after each setup's alert posts, call the router; if it returns a setup_dict, call `paper_broker.execute_signal(setup_dict)`. | One consumer; YAGNI on an event-bus refactor |
| Dedup state | Query `TradeRecorder` directly. No separate in-memory cache. `_open_count_by_book` (Phase 2b) + new `_entry_count_today_by_combo(strategy, dte_bucket)` helper. Restart-safe (state lives in persistent JSON). | Cleanest; no two sources of truth |

## Architecture

```
intraday_scanner.run()  (every 5 min, market hours)
        │
        │ for each setup in SPYOptionsEngine.analyze(...):
        │
        ├──► (existing) post alert via notifier.alert  [if conviction ∈ {high, standard}]
        │
        └──► (NEW) if INTRADAY_PAPER_BROKER_ENABLED and conviction == "high":
                  setup_dicts = intraday_entry_router.route(setup, now, trade_recorder)
                  # router returns 0, 1, or 2 setup_dicts (2 for ultra-conv double-DTE)
                  for sd in setup_dicts:
                      paper_broker.execute_signal(sd)
                      # paper_broker enforces MAX_CONCURRENT_DISCIPLINED cap separately
```

## Components

### `signals/intraday_entry_router.py` (new)

Pure-function decision module. Single responsibility: take a `SPYSetup` + current state + clock, return a list of zero, one, or two `setup_dict`s ready for `paper_broker.execute_signal`.

```python
def route(
    setup: SPYSetup,
    now: datetime,            # current ET datetime
    trade_recorder: TradeRecorder,
) -> list[dict]:
    """
    Apply entry-tier filter (conviction == 'high'), H2 DTE assignment, and
    D dedup rule (one open per combo; ≤ 2/day per combo).
    Returns 0..2 setup_dicts ready for execute_signal.
    """
```

Pure-function design means it's trivially unit-testable — every branch (morning vs afternoon vs Fri-PM vs ultra-conv, every dedup state) can be exercised with synthetic inputs.

### `scanners/intraday_scanner.py` (modify)

Inside `run()`, after each high-conviction setup posts its alert, call the router + maybe `execute_signal`. ~10-20 new lines, gated by `config.INTRADAY_PAPER_BROKER_ENABLED`.

### `config.py` (add 5 constants)

```python
# ── Phase 3: Intraday entry pipeline ────────────────────────────────────────
# Kill-switch for the intraday-scanner → paper_broker.execute_signal wiring.
# Default True (ship enabled); flip to False + commit to disable instantly.
INTRADAY_PAPER_BROKER_ENABLED = True

# Which conviction tier qualifies as an intraday entry. Configurable so we can
# later widen to include "standard" (=45-67 score) without code change.
ENTRY_TIER_MINIMUM = "high"   # one of "high" / "standard"

# H2 DTE assignment thresholds.
INTRADAY_DTE_MORNING_CUTOFF = "12:30"   # ET. Before -> 0DTE; after -> 1-3DTE
ULTRA_CONVICTION_DOUBLE_DTE_SCORE = 85  # score >= this opens both DTE buckets

# Position dedup (D rule).
INTRADAY_PER_COMBO_DAILY_CAP = 2        # max entries/day per (strategy, dte_bucket)
```

### `learning/paper_broker.py` (add 1 helper)

New helper `_entry_count_today_by_combo(strategy: str, dte_bucket: str) -> int` — counts how many trades were opened today for this (strategy, dte_bucket) combo. Reads from `TradeRecorder.get_trades_by(strategy=..., dte_bucket=...)` filtered to today's date. Used by the router's D-rule enforcement.

## Data Flow

1. `intraday_scanner.run()` triggered by the cron every 5 min.
2. `SPYOptionsEngine.analyze(df_15m, df_5m)` returns `list[SPYSetup]`.
3. For each setup:
   - Existing alert logic runs (Pushover/Discord) if `conviction ∈ {high, standard}` and score-improvement dedup allows.
   - **NEW:** if `config.INTRADAY_PAPER_BROKER_ENABLED` and `setup.conviction == "high"`:
     - Call `intraday_entry_router.route(setup, now, trade_recorder)`.
     - The router checks dedup state (open positions, today's entry count), applies H2 DTE assignment, returns 0..2 setup_dicts.
     - For each returned setup_dict, call `paper_broker.execute_signal(sd)`. Paper-broker enforces MAX_CONCURRENT_DISCIPLINED=3.
4. `[AUTO-PAPER]` trades are written to the trade journal with `dte_bucket ∈ {"0DTE", "1-3DTE"}` and `book="disciplined"`.
5. Phase 2b's intraday exit cron (every 5 min via `manage_open(dte_buckets=["0DTE", "1-3DTE"])`) picks up the new positions and applies the per-sub-strategy exit rules from Phase 1.

## H2 Rule (Detailed Algorithm)

```
def assign_dte_buckets(setup, now: datetime, is_friday: bool) -> list[str]:
    """Returns 1 or 2 DTE buckets the setup should open in."""

    # Ultra-conviction always opens both, EXCEPT Friday PM (no weekend exposure).
    is_friday_pm = is_friday and now.time() >= time(12, 30)
    if setup.score >= ULTRA_CONVICTION_DOUBLE_DTE_SCORE and not is_friday_pm:
        return ["0DTE", "1-3DTE"]

    # Friday PM safeguard: 0DTE only, regardless of time-of-day rule.
    if is_friday_pm:
        return ["0DTE"]

    # Default H2: time-of-day discriminator.
    morning_cutoff = parse_time(INTRADAY_DTE_MORNING_CUTOFF)  # 12:30 ET
    if now.time() < morning_cutoff:
        return ["0DTE"]
    return ["1-3DTE"]
```

## D Rule (Detailed Algorithm)

```
def dedup_filter(dte_buckets: list[str], strategy: str,
                  trade_recorder) -> list[str]:
    """Filter dte_buckets to those that pass the D dedup rule:
      - No position currently open in (strategy, bucket)
      - Today's entry count < INTRADAY_PER_COMBO_DAILY_CAP
    Returns the filtered list (0..2 buckets)."""

    allowed = []
    for bucket in dte_buckets:
        # Check #1: no position open in this combo
        if any_open(trade_recorder, strategy, bucket):
            continue
        # Check #2: today's entry count under cap
        if entries_today(trade_recorder, strategy, bucket) >= INTRADAY_PER_COMBO_DAILY_CAP:
            continue
        allowed.append(bucket)
    return allowed
```

The router composes `assign_dte_buckets` and `dedup_filter` then builds a setup_dict per surviving bucket. MAX_CONCURRENT_DISCIPLINED enforcement happens later inside `paper_broker.execute_signal` (Phase 2b code), so a cap-bound situation produces a `skipped_reason: "disciplined_book_cap"` return value rather than a router-level skip.

## Setup-dict shape (Phase 3 contract with `execute_signal`)

```python
{
    "date":        "2026-05-26",           # ET date string
    "strategy":    "call_debit_spread",    # one of call_debit_spread / put_debit_spread / iron_condor
    "dte_bucket":  "0DTE",                  # or "1-3DTE"
    "book":        "disciplined",
    "direction":   "bullish",               # or "bearish" / "neutral" (from setup.direction)
    "entry_price": 1.10,                    # derived from setup; placeholder until Phase 4 designs entry pricing per sub-strategy
    "max_profit":  200.0,                   # placeholder; refined in Phase 4
    "max_loss":    110.0,                   # placeholder; refined in Phase 4
    "legs":        [],                      # placeholder; structure builder in Phase 4
}
```

**Caveat:** the `entry_price` / `max_profit` / `max_loss` / `legs` are placeholders for Phase 3. `SPYOptionsEngine` doesn't currently build the leg structure — only the directional decision. Phase 3's `execute_signal` calls will store synthetic placeholder values for these fields so the trade is logged for dedup-state purposes. **Phase 4 will design the proper structure-construction step** (call into a `signals.options_layer`-style builder for each sub-strategy at the moment of entry). Until then, intraday paper trades carry placeholder pricing — they participate in `get_trades_by` queries and feed the per-strategy dedup, but their pnl numbers are not meaningful until Phase 4. **This is acceptable for Phase 3** because the test is whether the wiring + dedup + DTE assignment work, not whether the pnl number is right.

## Testing Strategy

- **Router unit tests (`tests/test_intraday_entry_router.py`):** synthetic `SPYSetup` + clock + trade-recorder state → expected list of setup_dicts. Cover every H2 branch (morning, afternoon, Friday-PM, ultra-conv, ultra-conv-on-Friday-PM) and every D branch (no positions / one open / per-day cap reached / multiple buckets some-allowed-some-not).
- **Scanner integration test (`tests/test_intraday_scanner_pipeline.py`):** stub `paper_broker.execute_signal` to record calls; verify the scanner produces the expected `execute_signal` invocations under feature-flag both off and on; verify the flag-off path is byte-identical to today (no execute_signal calls).
- **Feature-flag parity test:** with `INTRADAY_PAPER_BROKER_ENABLED = False`, the scanner's full run produces zero `execute_signal` calls — the bot is byte-identical to Phase 2b.
- **No backtest required for Phase 3** — this is wiring, not a strategy. Strategies (SPYOptionsEngine) and exit rules (Phase 1/2b) are already in place. Phase 4 will backtest the exit-feasibility design.

## Honesty Caveats (baked in)

- **`entry_price` / `legs` are placeholders.** The router produces a setup_dict that satisfies the `execute_signal` schema, but the pricing/legs values are synthetic until Phase 4 wires in a proper per-sub-strategy structure builder. P&L on Phase 3 intraday paper trades is not meaningful — the test of Phase 3 is whether the wiring + dedup + DTE assignment work as designed, not whether the trade numbers are accurate.
- **Single book — no learning book yet.** Every high-conviction setup that passes the rules opens in the disciplined book. We're not yet sampling marginal entries (that's Phase 4's dual-book work).
- **Exit-feasibility gate is deferred.** Phase 3 trusts the existing SPYOptionsEngine conviction tier as the only entry-quality gate. Whether a trade's exit path is plausible is a Phase 4 design question informed by Phase 3's real intraday paper-trade data.
- **Flag default ON.** First phase with live behavior change. Kill-switch is the safety belt.

## What's the bot doing Tuesday morning (with this shipped + flag ON)

- **09:15:** existing daily play — unchanged (Phase 1+2a+2b).
- **09:16:** existing paper_broker opens the 45DTE position — unchanged.
- **09:30 onward (every 5 min):** intraday_scanner runs. Alerts fire as today. **NEW:** when a high-conviction setup appears, the router applies H2+D and (assuming dedup + cap allow) `execute_signal` opens an intraday position tagged `dte_bucket="0DTE"` (morning) or `"1-3DTE"` (afternoon) or both (ultra-conv).
- **Every 5 min (intraday cron, Phase 2b):** strategy-aware `ExitManager` processes any open 0DTE/1-3DTE positions per the per-sub-strategy exit rules (Phase 1).
- **16:08:** existing daily cron — handles 45DTE positions.
- **19:00:** existing reflector — sees the new intraday trades in the journal.

## File Inventory

New: `signals/intraday_entry_router.py`, `tests/test_intraday_entry_router.py`, `tests/test_intraday_scanner_pipeline.py`.

Modified: `scanners/intraday_scanner.py` (inline wiring), `learning/paper_broker.py` (one new helper), `config.py` (5 new constants).

## Resolved Spec-Level Decisions

1. **Placeholder pricing values.** Hardcoded constants in the router: `entry_price=1.0`, `max_profit=200.0`, `max_loss=100.0`. Obvious "this is a placeholder" round numbers. Phase 4's structure builder replaces them with real per-sub-strategy values. Documented in code comments at the placeholder construction site.
2. **`is_friday_pm` lives inside the router.** Single source of truth for the H2 rule — the scanner just passes `now` and lets the router compute the day-of-week + time-of-day branches. Keeps the H2 rule one-file, one-function for testability.
3. **"Today" means today in ET.** Entry-count-by-combo is calculated against today's ET date (since the rest of the bot uses `pytz.timezone("US/Eastern")` for market-hours decisions). The helper compares `entry_date_ET == today_ET`.

## Phase 4 (out of scope, captured for follow-up)

- Dual-book design (entry-only `learning_broker` + disciplined `paper_broker`); the exit-feasibility predicate that discriminates between them.
- Per-sub-strategy exit-feasibility rules — design pass informed by 60+ days of Phase 3 paper-trade data.
- Structure-construction: replacing the Phase 3 setup_dict placeholders with real strikes/legs/pricing from a sub-strategy-aware builder.
- Notification routing for intraday paper trades (separately from existing scanner alerts).
- Reflector refactor for per-sub-strategy summarization.
