# Phase 4b — Intraday Structure Builder (real strikes + pricing) — Design

**Date:** 2026-05-31
**Status:** Design approved; ready for implementation plan.
**Branch (anticipated):** `phase4b-structure-builder`

## Problem

Phase 3's intraday entry router (`signals/intraday_entry_router.py`) emits
**placeholder** structures: `_build_setup_dict` bakes `entry_price=1.0`,
`max_profit=200`, `max_loss=100`, and a single synthetic strike-0 leg
(`_synthesize_legs`). Every intraday paper trade therefore has fictional
dollars — the journal's P&L on the intraday book is meaningless. This blocks:

- honest live intraday paper P&L, and
- the walk-forward edge question (the WF harness prices realistically, but the
  live path it's meant to model does not — so they can diverge).

A related hazard, now that auto-paper trades are correctly identified by the
`source` field (2026-05-30 fix): the `ExpiryResolver` will price the synthetic
strike-0 legs at intrinsic `0.0` and book a fabricated "+$100 win" per IC. So
the placeholder structures are not merely uninformative — once recognized they
actively pollute the journal. The builder removes the placeholders entirely on
the live path.

## Goal & Scope

**One structure builder, two pricing modes (live + historical), serving both the
live scanner and the walk-forward backtest from a single selection rule.**

In scope:
- A new `signals/intraday_structure_builder.py` producing real legs + pricing.
- Refactor `backtests/intraday_backtest.py` to use the shared selection (parity).
- Wire the live scanner seam so intraday paper trades get real structures.
- A same-day (0DTE) expiry sub-fix in `OptionsChain` so the live 0DTE bucket
  can price.

Explicitly OUT of scope (later specs, per the Phase 4b queue):
- Dual-book design + exit-feasibility predicate.
- Per-sub-strategy reflector summarization.
- Off-hours learner Option A.
- KB-validator item-4 tightening.
- Walk-forward edge **validation / threshold calibration** — that gates real
  capital, not honest paper trades, and is run after this ships.

## Key Decisions

1. **Strike selection: spot-offset, identical in both modes.** Reuses the
   proven `build_0dte_legs` geometry so the backtest faithfully predicts live
   (the WF apples-to-apples invariant). Offsets remain fixed builder constants
   for now (parity + YAGNI); promote to `config.py` only when a hypothesis
   wants to tune them.
2. **Pricer split.** Selection is shared; only the price source differs —
   `LiveChainPricer` (snapshot mids) vs `HistoricalPricer` (per-contract
   aggregates). This is what lets one builder serve both worlds.
3. **Build at the scanner seam, not in the router.** `route()` / `route_explain()`
   stay pure (no chain/IO), preserving the Phase 1 tracking surface. The scanner
   materializes the structure between `route()` and `execute_signal`.
4. **Honesty on failure.** If a structure can't be priced (no/empty chain,
   illiquid leg, unresolved expiry, pricing error), `build_structure` returns
   `None`; the scanner skips the entry and logs it. Never record a placeholder.
5. **Router placeholders retained as a documented fallback.** `_build_setup_dict`
   keeps its placeholder values (always overwritten by the builder on the live-ON
   path) to keep `route_explain`/tracking tests stable; a follow-up removes them
   once the live builder has clean sessions.

## Architecture

### New module: `signals/intraday_structure_builder.py`

Three units:

**`select_legs(strategy, dte_bucket, spot) -> list[LegSpec]`** — pure, no I/O.
Generalizes `build_0dte_legs`, preserving exact geometry:
- `iron_condor` → SELL P `k(-3)`, BUY P `k(-8)`, SELL C `k(+3)`, BUY C `k(+8)`
  (short OTM 3, wing 5)
- `call_debit_spread` → BUY C `k(0)`, SELL C `k(+3)`
- `put_debit_spread` → BUY P `k(0)`, SELL P `k(-3)`

where `k(x) = round(spot + x)` ($1 SPY strikes). Owns the router-name→geometry
mapping (`call_debit_spread`→bull_debit, `put_debit_spread`→bear_debit,
`iron_condor`→iron_condor) currently duplicated in the WF as
`_strategy_to_structure`.

**Pricer interface** — `price(legs, dte_bucket, spot, as_of) -> StructurePricing | None`:
- `LiveChainPricer(OptionsChain)` — resolves the concrete expiry from the
  snapshot chain (incl. same-day for 0DTE), marks each leg at its snapshot mid.
- `HistoricalPricer(OptionsHistory)` — resolves expiry (0DTE→`as_of` day;
  1-3DTE→real listed expiry within range), marks each leg from `get_aggs` at the
  entry timestamp.

`StructurePricing` = `{legs (with expiration + per-leg mid), entry_price,
max_profit, max_loss}`.

**`build_structure(strategy, dte_bucket, spot, pricer) -> dict | None`** —
composes `select_legs` (geometry) with `pricer` (expiry + pricing + risk). The
single entry point both the live scanner and the backtest call.

### Pricing math (shared)

- `entry_price` (net per-share premium):
  - credit (iron_condor, credit spread): `Σ short mids − Σ long mids`
  - debit (debit spread): `Σ long mids − Σ short mids`
- credit: `max_profit = entry × 100`, `max_loss = (wing − entry) × 100`
- debit: `max_profit = (width − entry) × 100`, `max_loss = entry × 100`

Identical to the formula already in `_simulate_short_dte_with_expiration`.

### OptionsChain 0DTE sub-fix

`find_iron_condor` / `find_vertical_spread` compute
`min_exp = today + timedelta(days=max(1, dte_target - dte_tolerance))`, which
excludes same-day expiries. The `LiveChainPricer` path must allow `min_exp =
today` when `dte_target == 0` (0DTE bucket). Tighten `dte_tolerance` for the
intraday buckets so a "1-3DTE" play cannot pick a far expiry.

## Data Flow

**Live (new wiring):**
```
intraday_scanner (every 5 min, INTRADAY_PAPER_BROKER_ENABLED gate)
  → route(setup, now, broker)                      # pure; routed setup_dicts
  → for each routed dict:
       spot  = current SPY price (scanner has it)
       built = build_structure(strategy, dte_bucket, spot, LiveChainPricer(chain))
       if built is None: log "<strategy>/<bucket> unpriceable — skipped"; continue
       merge built {legs, entry_price, max_profit, max_loss} into setup_dict
  → execute_signal(enriched_dict)                  # records REAL legs + pricing
```
Wrapped in the existing Phase 3 try/except — a builder bug cannot crash the
scanner.

**Backtest (refactor for parity):**
```
intraday_router_wf → simulate_short_dte_day → simulate_0dte_day /
                     _simulate_short_dte_with_expiration
  build_0dte_legs(spot, structure)  ──►  select_legs(strategy, dte_bucket, spot)
  ...existing per-leg get_aggs marking + exit simulation unchanged
```
Only the selection call is swapped; entry/exit marking stays. `select_legs`
reproduces `build_0dte_legs` geometry exactly → byte-identical results.

## Error Handling

- `build_structure → None` on: no/empty chain, leg with no quote / zero
  liquidity, expiry unresolved, any pricing exception.
- Live: scanner logs and opens nothing — never a placeholder trade.
- Backtest: already returns `None` → that day isn't counted (unchanged).

## Testing (TDD)

Real code where possible; fakes only for injected chain/history providers.

- **`select_legs`** — geometry parity per `(strategy, dte_bucket)`: leg counts,
  strikes at the right offsets, $1 rounding, correct `cp`/`action`, router-name
  mapping.
- **`LiveChainPricer`** (fake chain) — credit/debit + max_profit/max_loss math;
  resolves same-day 0DTE expiry; `None` on empty chain / missing leg quote.
- **`HistoricalPricer`** (fake history) — prices from aggregates at entry ts;
  `None` when a leg has no data.
- **`build_structure`** — integration: full dict given a working pricer; `None`
  when the pricer returns `None`.
- **Parity guard** — `select_legs(...) == build_0dte_legs(...)` for every
  structure; existing `intraday_router_wf` tests stay green.
- **Scanner seam** — structure → `execute_signal` records real legs/pricing;
  `None` → no trade recorded + logged (asserts the no-fake-trades guarantee).

## Deploy & Activate (after implementation)

1. Restart `smta.service` to load new code.
2. Confirm `INTRADAY_PAPER_BROKER_ENABLED=True` (already set).
3. Watch the first live session: new intraday trades carry real legs/pricing;
   `ExpiryResolver` closes them at real intrinsic.

Until this ships, intraday should stay disabled to avoid the synthetic-leg
fake-P&L hazard described above.

## Follow-ups (noted, not in this spec)

- Remove the router placeholder values from `_build_setup_dict` once the live
  builder has clean sessions.
- Promote strike offsets to `config.py` if/when tuning is wanted.
- Walk-forward edge validation + threshold calibration (gates capital).
- The remaining Phase 4b queue items (dual-book, per-sub-strategy reflector,
  off-hours Option A, KB-validator tightening).
