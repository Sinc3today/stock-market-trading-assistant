# Phase 4a — Learning-Loop Hygiene Design

**Date:** 2026-05-23
**Status:** Draft for user review
**Ship target:** Tuesday 2026-05-26 market open

## Goal

Improve the learning loop's signal quality, cost profile, and resilience **without changing the bot's live trading behavior**. Phase 3 (intraday entry pipeline) shipped Tuesday-live; Phase 4a is internal hygiene so the loop processing Phase 3's output is honest, cheap, and durable.

## Constraints

1. **Zero live behavior change.** Nothing in this phase affects what trades the bot opens or how it exits. If anything breaks, it breaks the learning loop's output quality, not the bot's trading decisions.
2. **Walk-forward discipline preserved.** Anything we learn from historical data must use chronological IS/OOS splits — never in-sample-fit thresholds we then ship.
3. **Phase 3's known limitations stay limitations.** Placeholder pricing (entry_price=1.0, max_profit=200, max_loss=100, legs=[]) is not in scope to fix here. That's Phase 4b's structure-builder work.

## Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Backfill strategy** | Path 2 (lightweight: re-tag existing daily backtest output) | Captures the bootstrap idea for 45DTE without pulling Phase 4b's structure builder forward. Intraday sub-strategies stay data-starved until Phase 4b. |
| **Reflector routing (item 5)** | Option 1 (anomaly hybrid: phi4 daily, Sonnet on anomalies) | Best long-term design. Calibration risk acknowledged — thresholds start as config constants; tunable as Phase 3 + backfill data accumulates. |
| **Off-hours learner reframe (item 6)** | Option B (regime-drift detection) | Always has data regardless of trade volume. Catches the highest-leverage signal — meta-shifts that affect all sub-strategies. Option A (cross-sub-strategy patterns) deferred to Phase 4b once full-fidelity backfill exists. |
| **KB confidence cap** | 0.7 hard cap on single-day entries (kind:"daily") | Single-day evidence is insufficient for high-confidence claims per the walk-forward discipline memory. Higher confidence (>0.7) reserved for hypothesis_engine and off_hours_learner outputs (multi-day corroboration). |
| **KB evidence-citation enforcement** | Soft warning initially | Log violations but accept entries. Tighten to hard rejection in Phase 4b after observing failure rates. |
| **Caching scope** | All four Sonnet callers (reflector, hypothesis_engine, off_hours_learner, ai_advisor) | One change, captured across the board. |
| **Phase 4b backfill** | Path 1 (full-fidelity 60d Phase 3 pipeline replay with real Polygon historical option prices) | When? After Phase 4b's structure builder lands. Re-runs become canonical, not placeholder. |

## Item-by-Item Design

### Item 0 — 60d lightweight backfill

**Purpose:** Foundation for items 2 and 5. Off-hours learner item 6 does NOT depend on this (regime-drift uses regime classifications, always present).

**Architecture:**
- Source: existing `backtests/spy_daily_backtest.py` output over last 60 trading days
- Output: JSONL records matching `journal/trade_recorder.py` schema, with a `simulated: true` flag and Phase 2a tags (`strategy="iron_condor"`, `dte_bucket="45DTE"`, `book="disciplined"`)
- File: `logs/learning/simulated_trades.jsonl`
- Refresh cadence: one-shot manual seed in Phase 4a; weekly auto-refresh deferred to Phase 4b
- Consumer integration: extend `TradeRecorder.get_trades_by()` (or add `get_trades_unioned()`) to optionally include simulated records

**Honest gap:** Path 2 only covers 45DTE. The 6 intraday sub-strategies remain data-starved until Phase 3 accumulates real trades or Phase 4b's Path 1 backfill ships.

**Distinguishability:** consumers can filter via the `simulated` flag — learning loop reads both, P&L reports filter to `simulated=false`.

### Item 1 — Prompt caching

**Modules:** `learning/reflector.py`, `learning/hypothesis_engine.py`, `learning/off_hours_learner.py`, `alerts/ai_advisor.py`

**Approach:** Add `cache_control={"type": "ephemeral"}` markers on the static prompt portions in each Sonnet API call:
- Cacheable: system prompt, JSON schema, KB rules, model role instructions
- Uncached: today's trades, today's observations, today's predictions (dynamic per-call portion)

**Validation gate:** byte-identical output from Sonnet before/after caching on a fixed set of test inputs (parity test). Caching must not change behavior — only cost.

**Cost expectation:** ~25-30% end-to-end cost reduction (input drops ~80%, output is unchanged and dominates cost). Real win is enabling Phase 4b's anomaly-hybrid scaling without prompt-growth cost penalty.

### Item 2 — rolling_accuracy per-sub-strategy

**Module:** `learning/hypothesis_engine.py` (and wherever `rolling_accuracy` is computed/consumed)

**Change:** replace scalar `rolling_accuracy: float` with `rolling_accuracy: dict[str, float]` keyed by `"strategy:dte_bucket:book"` string (e.g., `"iron_condor:45DTE:disciplined"`).

**Data sources:** `logs/learning/predictions.jsonl` + `journal/trade_logger.py` output + (now) `logs/learning/simulated_trades.jsonl` from item 0.

**Backward compatibility:** keep aggregate available as `rolling_accuracy["all"]` for any caller that wants the global number.

**Why it matters:** hypothesis_engine can now propose targeted parameter tuning at sub-strategy granularity instead of blunt global adjustments.

### Item 3 — Confidence-cap on single-day KB entries

**Module:** `learning/reflector.py` (post-Sonnet-response validator)

**Validator behavior:** After Sonnet returns the JSON reflection, walk each KB entry; if `kind == "daily"`, cap `confidence` field at 0.7. Log when cap fires.

**Why 0.7:** chosen as the convention; above 0.7 reserved for multi-day-corroborated entries from hypothesis_engine (Saturday) and off_hours_learner (Sunday).

**Failure mode:** silent cap (we don't reject the entry; we just clamp). Raw Sonnet response still saved per project rule #14.

### Item 4 — Evidence-citation discipline

**Module:** same validator pass as item 3 (one file change, two checks).

**Validator behavior:** each KB entry must have an `evidence` field that references one of:
- An actual trade_id from today's records
- A specific number from today's data (e.g., "SPY closed at 587.42")
- A specific KB entry from a previous day (forward-link)

**On violation:** log warning, accept the entry anyway (soft enforcement for Phase 4a). Counter of violations exposed in BUILD_LOG metrics.

**Tightening path:** if violation rate stays <5% over 2 weeks, promote to hard rejection in Phase 4b.

### Item 5 — Local-first reflector (Option 1: anomaly hybrid)

**Architecture:** phi4 (local Ollama @ nucbox) handles the daily reflection JSON. Sonnet escalation triggered by anomaly detector.

**Anomaly triggers (config constants for easy tuning):**

```python
# config.py — Phase 4a
REFLECTOR_ANOMALY_STOPS_MIN = 2          # ≥N stop-outs today
REFLECTOR_ANOMALY_PRED_MISS_PCT = 1.5    # |predicted - actual| / actual > N%
REFLECTOR_ANOMALY_NEW_SUBSTRATEGY = True # any sub-strategy fired for the 1st time
REFLECTOR_ANOMALY_REGIME_CHANGE = True   # regime classification differs from yesterday
```

**Calibration:** triggers chosen as conservative defaults. Once backfill data (item 0) is loaded, can recalibrate `STOPS_MIN` against 45DTE 60d distribution. Intraday triggers stay conservative until Phase 4b backfill.

**Routing logic:**
1. Daily reflection prompt prepared identically for both models
2. Anomaly detector runs against today's facts (trades, prediction, regime)
3. If any trigger fires → Sonnet; else → phi4
4. Both paths produce same JSON schema; items 3+4 validator runs on either output
5. If phi4 fails (JSON parse error, schema violation) → auto-escalate to Sonnet as fallback

**Fallback safety:** Sonnet path is the proven path. Local path is additive — failure cannot regress the loop.

**Cost expectation:** ~1-3 Sonnet calls/week instead of 5. Combined with item 1's caching: estimated ~60-70% total reflector cost cut.

### Item 6 — Off-hours learner reframe (Option B: regime-drift)

**Module:** `learning/off_hours_learner.py`

**Schedule:** Sunday 10:00 (existing slot, unchanged)

**Prompt reframe:** load 120d of SPY daily regime classifications. Compare last-60d regime distribution to prior-60d. Identify meta-shifts.

**Sample output schema:**
```json
{
  "kind": "regime_drift",
  "window_recent": "2026-03-25 to 2026-05-23",
  "window_prior": "2026-01-24 to 2026-03-24",
  "shifts": [
    {
      "regime": "TRENDING_LOW_VOL",
      "recent_pct": 18.3,
      "prior_pct": 32.1,
      "delta_pct": -13.8,
      "implication": "..."
    }
  ],
  "confidence": 0.75,
  "evidence": ["regime_classifications source: signals/regime_detector.py over 120d"]
}
```

**Walk-forward discipline:** this is observation, not parameter selection. We're NOT auto-tuning anything from the drift report — that would be in-sample fitting. The output goes into the KB for hypothesis_engine to consider on Saturday, with its own IS/OOS discipline applied to any parameter proposal.

## Implementation Order

Order chosen to minimize rework + maximize parallelizability:

1. **Item 0 (backfill seed)** — first, since items 2 and 5 read from it
2. **Item 1 (caching)** — independent, can run parallel with anything
3. **Item 2 (rolling_accuracy per-sub-strategy)** — uses item 0 data
4. **Item 3 + Item 4 (KB validator)** — single file change, ship together
5. **Item 5 (local-first reflector)** — uses item 0 baseline (where applicable) + items 3/4 validator
6. **Item 6 (off-hours regime-drift)** — independent, can be parallel with 5

Estimated total: ~4 days. Saturday + Sunday + Monday gives us the window.

## Out of Scope (Phase 4b)

Explicitly NOT in Phase 4a — these are queued for next weekend with a cron reminder set for Sat 2026-05-30:

- **Path 1 full-fidelity 60d Phase 3 pipeline replay** with real Polygon historical option prices (intraday sub-strategy backfill)
- **Per-sub-strategy structure builder** — replace Phase 3's placeholder pricing (entry_price=1.0, max_profit=200, max_loss=100, legs=[]) with real strikes/legs per (strategy, dte_bucket)
- **Dual-book design** with exit-feasibility predicate (the disciplined-vs-learning split)
- **Per-sub-strategy reflector summarization** (per-sub-strategy KB synthesis in reflector)
- **Off-hours learner Option A** (cross-sub-strategy pattern detection) — added alongside Option B in a second Sunday slot
- **Tightening item 4 from soft warning to hard rejection** (after observing violation rates for 2 weeks)
- **Auto-refresh cadence for item 0 backfill** (weekly cron rather than manual seed)

## Test Coverage Plan

Each item ships with TDD-ordered tests:

- **Item 0:** seed-script unit tests, simulated_trades.jsonl schema validation, get_trades_by() integration test with mixed real+simulated records
- **Item 1:** parity tests (byte-identical Sonnet output before/after caching) for each of the four caller modules
- **Item 2:** unit tests for per-sub-strategy aggregation, dict shape, "all" backward-compat alias
- **Item 3+4:** unit tests for the validator — confidence cap fires correctly, evidence violations log but don't reject, soft enforcement metric exposed
- **Item 5:** unit tests for anomaly detector (each trigger fires/doesn't fire as expected), integration test for routing + fallback escalation
- **Item 6:** unit tests for window comparison logic, regime distribution math, output JSON schema validation

**Baseline at branch start:** 771 tests passing (Phase 3 end). Phase 4a should add ~25-35 tests.

## Risk Register

| Risk | Mitigation |
|---|---|
| Item 5 anomaly triggers wrong → either always-Sonnet (no savings) or never-Sonnet (KB degradation) | Triggers are config constants. Monitor escalation rate in first week; tune if not landing in 20-60% range |
| Item 0 backfill format drift from real paper_broker records | Schema validation test enforces parity; backfill schema is a snapshot of `trade_recorder.py`'s output as of Phase 4a start |
| Item 1 caching breaks Sonnet output | Parity tests are the gate; any byte-diff blocks merge |
| Item 6 regime-drift report is "always shifting" or "never shifting" — uninformative | Minimum delta threshold (≥10%) to flag as a shift; output empty `shifts: []` if nothing significant. Better silent than noisy |
| Cumulative complexity adds risk to live bot | All Phase 4a items are off the trading path. Kill-switch flag from Phase 3 still independently controls live behavior |

## Phase 4b Forward Link

Cron reminder scheduled for Sat 2026-05-30 10:03 (session-only — user should set calendar backup). When Phase 4b begins, the structure builder is the unblocking item: once it lands, Path 1 backfill can run, which then unlocks Option A (cross-sub-strategy patterns) in the off-hours learner, which then unlocks per-sub-strategy reflector summarization. The chain is: structure builder → Path 1 backfill → Option A → per-sub-strategy summarization.
