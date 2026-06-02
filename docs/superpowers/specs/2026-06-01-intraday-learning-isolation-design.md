# Intraday Learning Isolation — Dual-Book + Per-Sub-Strategy Falsificationist Reflector — Design

**Date:** 2026-06-01
**Status:** Design approved; ready for implementation plan.
**Branch (anticipated):** `intraday-learning-isolation`

## Problem & Goal

Intraday entry is already isolated from the daily/larger-timeframe regime (the
intraday engine and router consult no daily regime/plan/prediction). But the
*learning* side is not yet fully isolated, and the loop is biased toward
confirmation:

1. **Dual-book unused.** `PaperBroker.execute_signal` honors a `book` tag
   (disciplined cap 3 / learning cap 6), but the router hardcodes
   `book="disciplined"`, so every intraday entry lands in the "real-money proxy"
   book. There is no mechanism to route marginal entries elsewhere.
2. **Reflector is blended and confirmation-seeking.** One daily LLM reflection
   covers all books at once, and it asks "what did we learn / what worked" —
   never "what would disprove what we believe."

**Goal:** isolate intraday learning per sub-strategy and make it
*falsificationist* — actively seeking disconfirming evidence rather than waiting
for our priors to be confirmed.

This spec covers two woven pieces:
- **Dual-book** with a per-`(strategy, dte_bucket)` exit-feasibility predicate;
  the learning book reframed as a **falsification sandbox** (the trades the
  disciplined book refuses, taken in paper to generate disconfirming evidence).
- **Per-sub-strategy falsificationist reflector**: one isolated LLM reflection
  per active sub-strategy, each running a disconfirmation pass; KB entries tagged
  by sub-strategy and stance.

**Out of scope (next thread):** shadow-testing standing *daily* gates (the
extension gate) — the counterfactual "trade we didn't take" mechanism — lives in
the daily/prediction path and gets its own spec. Also out: capturing
tier-gate-refused setups in the sandbox (would require `route()` to emit
sub-tier setups; deferred).

## Guiding principle (why)

Waiting for an expected condition to come true (e.g. the extension gate waiting
for a pullback for 9+ sessions) loses opportunities and never tests the belief.
The trades we *skip* are exactly the data that would disprove the belief — and we
never record them. The learning book + falsificationist reflector exist to
capture and interrogate that disconfirming evidence per sub-strategy.

## Key Decisions

1. **One cohesive spec** — dual-book and per-sub-strategy reflector share the
   `strategy:dte_bucket:book` tagging and the isolation goal.
2. **Exit-feasibility predicate keyed per `(strategy, dte_bucket)`** — config-driven
   thresholds, calibratable later by the intraday WF.
3. **Predicate runs at the scanner seam** (post-pricing), not in the pure router.
4. **Reflector: separate LLM call per active sub-strategy** (local phi4-first),
   active-only to contain cost.
5. **Skip-day standby reflection still runs** every weekday (Rule 15) on the
   local model — no-trade-day learnings (why we skipped, near-misses) are
   valuable and otherwise hard to capture.
6. **Falsification woven in**: disconfirmation pass in every reflection; KB
   `stance` field; learning book = falsification sandbox (feasibility-refused).

## Architecture

### Dual-book

**New module `signals/exit_feasibility.py`** (pure, no I/O):
```
assign_book(strategy, dte_bucket, max_profit, max_loss, *, profit_target_pct) -> "disciplined" | "learning"
```
- Looks up `config.INTRADAY_FEASIBILITY[(strategy, dte_bucket)]` →
  `{"min_target_dollars": float, "min_rr": float}`.
- **disciplined** iff `profit_target_pct * max_profit >= min_target_dollars`
  AND `(max_profit / max_loss) >= min_rr` (guard `max_loss > 0`); else **learning**.
- `profit_target_pct` is supplied by the caller from
  `exit_manager._exit_rule_for(strategy, dte_bucket)["profit_target_pct"]`, so
  feasibility uses the *same* target the ExitManager will actually apply.
- Total function: a `(strategy, dte_bucket)` absent from the config → permissive
  default `{"min_target_dollars": 0.0, "min_rr": 0.0}` → disciplined. Never throws.

**Config `INTRADAY_FEASIBILITY`** — a dict keyed by the 6
`(strategy, dte_bucket)` combos (call_debit_spread/put_debit_spread/iron_condor ×
0DTE/1-3DTE). **Defaults permissive** (all thresholds 0.0 → everything
disciplined) so live behavior is unchanged until the intraday WF calibration
populates real values — mirroring the router_wf `MIN_*`-deferred pattern.

**Wiring (`scanners/intraday_scanner.py`):** after
`enriched = build_intraday_structure(...)` (real pricing known):
```
pt = exit_rule_for(enriched["strategy"], enriched["dte_bucket"])["profit_target_pct"]
enriched["book"] = assign_book(enriched["strategy"], enriched["dte_bucket"],
                               enriched["max_profit"], enriched["max_loss"],
                               profit_target_pct=pt)
broker.execute_signal(enriched)
```
The router's hardcoded `"book": "disciplined"` in `_build_setup_dict` stays as the
pre-pricing default (overwritten at the seam, like the pricing placeholders).
`execute_signal` already applies the correct per-book cap. Learning-book trades
are full paper trades tagged `book="learning"`, excluded from disciplined
"real-money proxy" stats but retained for training.

### Per-sub-strategy falsificationist reflector

**Restructure `learning/reflector.py::reflect_today`:**
1. **Active set** — `strategy:dte_bucket` keys among trades touched today
   (opened/closed/open). 45DTE daily combos fall out naturally (uniform).
2. **Per active sub-strategy → one isolated LLM call** (phi4-first via existing
   `call_llm` routing). The reflection unit is `strategy:dte_bucket` **across both
   books** — this is deliberate: it lets each reflection directly compare the
   *disciplined* outcomes against the *learning-book (refused)* outcomes for the
   same sub-strategy, which is the falsification comparison ("we refused these —
   did the refusal hold up?").
   - Scoped context: only that sub-strategy's trades (today + recent) from BOTH
     books, both `accuracy(by_substrategy=True)` slices for the combo
     (`strategy:dte_bucket:disciplined` and `:learning`), and KB entries tagged
     for it.
   - Prompt requires BOTH a "what worked" pass AND a **disconfirmation pass**:
     *"What belief did today's data challenge? What would have to be true for this
     sub-strategy's gate/threshold to be wrong? What evidence would disprove our
     current stance?"*
   - Output: KB entries (each carrying `sub_strategy` + `stance`) and a markdown
     file `logs/learning/reflections/YYYY-MM-DD/<strategy>__<dte_bucket>.md`.
3. **Skip-day standby** — if no active sub-strategies, run one standby reflection
   (local model) on the prediction outcome + *why we skipped* + near-misses
   (disconfirmation pass included). Preserves the weekday heartbeat.

**`KBEntry` schema additions** (`learning/knowledge_base.py`):
- `sub_strategy: str | None` — the `strategy:dte_bucket` key (None for
  standby/market entries).
- `stance: str | None` — `"confirming"` | `"disconfirming"` | None.
Both round-trip through `knowledge.jsonl`; existing entries without them load as
None (back-compat).

## Data Flow

**Dual-book (live, intraday scan):**
```
route() [pure, book="disciplined" default]
  → build_intraday_structure() [real max_profit/max_loss]
  → assign_book(...) → enriched["book"]
  → execute_signal() [records book, applies per-book cap]
```

**Reflector (19:01 ET):**
```
gather active sub-strategies from today's trades
  → for each: scoped context → phi4-first LLM call → tagged KB + per-sub MD
  → if none active: standby reflection (prediction/skip/near-miss)
```

## Error Handling

- `assign_book` pure and total — unconfigured combo → permissive default;
  `max_loss <= 0` guarded (treat R/R as failing → learning); never throws.
- Each per-sub-strategy reflector call wrapped independently — one sub-strategy's
  LLM/parse failure does not abort the others; its raw reply is still saved to
  disk (Rule 14). The standby path always runs even if a per-sub call fails.
- Scanner seam: `assign_book` is called only on a successfully priced `enriched`
  setup (None setups already skipped upstream); wrapped in the existing Phase 3
  try/except so it can't crash the scanner.

## Testing (TDD)

- **`assign_book`**: disciplined when target-$ AND R/R clear the per-combo
  thresholds; learning when either fails; permissive default for unconfigured
  combos; `max_loss=0` → learning; uses the passed `profit_target_pct`.
- **Scanner seam**: feasible enriched setup → `execute_signal` called with the
  expected `book`; learning cap (6) vs disciplined cap (3) honored; pre-pricing
  default overwritten.
- **Reflector**: active-set detection from trades; one call per active
  sub-strategy; KB entries carry `sub_strategy` + `stance`; per-sub-strategy MD
  written; **no active → standby reflection still runs**; one failing
  sub-strategy doesn't sink the rest; disconfirmation pass present in the prompt.
- **KBEntry**: `sub_strategy` + `stance` round-trip through `knowledge.jsonl`;
  legacy entries load with None.

## Follow-ups (noted, not in this spec)

- **Shadow-test standing daily gates (extension gate)** — counterfactual
  "trade we didn't take," scored; if the shadow beats the gate, flag the gate as
  bias for the hypothesis engine. (Daily/prediction path — next thread.)
- Capture tier-gate-refused intraday setups in the sandbox (requires `route()`
  to emit sub-tier setups tagged learning).
- Intraday WF calibration populates `INTRADAY_FEASIBILITY` per combo.
- Surface `stance` balance per sub-strategy (a sub-strategy accumulating mostly
  disconfirming evidence = a gate under pressure → hypothesis-engine candidate).
