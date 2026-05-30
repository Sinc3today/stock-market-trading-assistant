# Router `route_explain()` + Decision Tracking Surface — Design Spec

**Date:** 2026-05-30
**Status:** Approved design, pending implementation plan
**Author:** brainstormed with Claude Code

---

## Problem / Motivation

`signals/intraday_entry_router.py::route()` is a black box. It applies three gates in order — tier filter → DTE assignment → dedup — and returns only the **surviving** `setup_dict`s (0..2). It discards *why* a candidate was rejected. So today we can see what the router accepted, but never:

- how often each gate fires (is the tier filter doing the work, or is dedup the real bottleneck?),
- which setups were *almost* tradeable,
- the distribution of rejections across conviction / direction / time-of-day.

The companion walk-forward (`backtests/intraday_router_wf.py`, 2026-05-28) and its threshold calibration (`backtests/calibrate_router_wf.py`, spec 2026-05-29) measure the **PnL** of accepted trades. Neither records the *gating behavior* — the rejected side of the ledger. That gating distribution is exactly what we need to (a) sanity-check the calibration verdict, (b) feed the nightly reflector with observability the autonomous loop can narrate, and (c) make the deferred **multi-tick replay** investigation cheap when/if it's warranted.

This spec adds a **decision tracking surface**: a non-mutating `route_explain()` that exposes the full per-candidate decision trace, a live tracker that records the bot's real daily routing decision, an offline backfill over cached 2024 history, and a parquet rollup the calibration/analysis tooling can read.

This is sequencing step 1 of the locked plan: **tracking surface now → calibration (spec 2026-05-29) → multi-tick replay only if needed** (per commit a921503; see calibration spec §"If Calibration Says 'Shelve' — Investigate Multi-Tick First").

## Goals

- Add `signals/intraday_entry_router.route_explain(setup, now, broker)` — a pure function returning the full decision trace (accepted buckets + rejected buckets with the gate that blocked each), **without changing `route()`'s observable behavior**.
- Capture the bot's real daily routing decision live, with rejection reasons, as append-only JSONL under `logs/learning/` so the 19:00 ET reflector can read and narrate it.
- Backfill the trace over the 252 cached 2024 trading days (offline replay through `route_explain()`), so analysis has a full year immediately.
- Provide a rollup that compacts JSONL + backfill into a single parquet under `backtests/.cache/` for the calibration / analysis tooling.
- Stay DRY: `route_explain()` reuses the existing gate helpers; no gate logic is duplicated.

## Non-Goals (deferred)

- **Multi-tick replay** — `route_explain()` is evaluated at the same single decision point the live bot uses (09:16 ET from the plan) and that the backfill mirrors (09:45 ET post-OR). Re-evaluating at every 5-min bar is the explicit *conditional* step 3, gated on the calibration verdict. The tracking surface is designed so multi-tick becomes a loop over timestamps later, but v1 does not add it.
- **Regime in the trace record** — `SPYSetup` carries no `regime` field (confirmed: it has `trend`, `score`, `conviction`, `direction`, not regime). The record stores `trend` as a lightweight proxy. Regime-conditional analysis is deferred, consistent with the calibration spec's deferral of regime-conditional thresholds (needs `regime_breakdown` in `window_stats` first).
- **Changing what the router accepts** — this is pure observability. `route()`'s accept/reject decisions are unchanged; we only expose the reasons.
- **New scheduler cadence** — the live tracker piggybacks the existing paper-broker job (09:16 ET, already part of the learning loop). No new APScheduler interval.
- **Live wiring of any behavior change** — recording decisions changes no trading behavior.
- **Web-app surfacing of the trace** — the dashboard can read the parquet later; not in this spec.

## Locked Decisions

| Decision | Choice | Source |
|---|---|---|
| How rejection reasons get out | New sibling `route_explain()`; `route()` behaviorally untouched | Brainstorm: user chose "Sibling explain() function" |
| Where the record is written | JSONL live + parquet rollup | Brainstorm: user chose "Both — JSONL now, parquet rollup" |
| Live JSONL location | `logs/learning/router_track/YYYY-MM-DD.jsonl` (append-only) | `logs/learning/` is the reflector's read root |
| Rollup parquet location | `backtests/.cache/router_track/router_track.parquet` | Alongside the cached bars the analysis reads |
| Live integration point | `learning/paper_broker.py::_phase3_route_impl` — the existing single seam | Confirmed only live `route()` call site; designed as a monkeypatch seam |
| Live cadence | Once/day at 09:16 ET via existing paper-broker job (learning loop) | The bot makes one real routing decision/day from the plan |
| Backfill source | `backtests/router_setup_builder.build_historical_setup` → `route_explain` over a date range | Reuses the WF's full-fidelity historical SPYSetup factory |
| Backfill evaluation time | 09:45 ET post-OR (matches the WF's tick-of-day) | Apples-to-apples with `intraday_router_wf` |
| Rollup trigger | On-demand CLI (no auto-schedule in v1) | Honest-ML discipline: analysis is a deliberate step |
| Regime in record | Omitted; store `trend` proxy | `SPYSetup` has no regime field |
| DRY for dedup reasons | Extract `_dedup_partition()`; `_dedup_filter()` becomes a thin wrapper over it | Keeps one dedup implementation; `route()` output identical |
| Failure isolation | Tracker write wrapped in try/except at the seam | Standing Rule #10 — a tracker failure must never block paper execution |

## Architecture

```
signals/intraday_entry_router.py        (MODIFY — additive)
    ├── _dedup_partition(strategy, buckets, broker)
    │       → (allowed: list[str], rejected: list[tuple[str, str]])   # NEW shared primitive
    ├── _dedup_filter(strategy, buckets, broker)                       # REFACTOR → wrapper over _dedup_partition
    │       → [b for b, _ in _dedup_partition(...)allowed]             # behavior identical, route() untouched
    ├── route(setup, now, broker)                                      # UNCHANGED
    └── route_explain(setup, now, broker) → DecisionTrace (dict)       # NEW pure function

learning/router_tracker.py              (NEW)
    ├── build_trace_record(setup, trace, now, source) → dict          # flatten DecisionTrace → JSONL row
    ├── write_trace(record, day=None) → str                           # append to logs/learning/router_track/<day>.jsonl
    └── (no scheduler here; invoked from the paper_broker seam)

learning/paper_broker.py                (MODIFY — 1 seam)
    └── _phase3_route_impl(setup, now, broker)                         # call route_explain, write trace, return accepted

backtests/router_track_backfill.py      (NEW)
    └── backfill(start, end, out_jsonl=None) → int                    # replay build_historical_setup → route_explain
        if __name__ == "__main__": CLI

backtests/router_track_rollup.py        (NEW)
    ├── load_jsonl_dir(dir) → list[dict]
    ├── rollup_to_parquet(records, out_path) → str
    └── if __name__ == "__main__": CLI (reads logs/learning/router_track/*.jsonl + backfill jsonl → parquet)

tests/test_route_explain.py             (NEW)
tests/test_router_tracker.py            (NEW)
tests/test_router_track_backfill.py     (NEW)
tests/test_router_track_rollup.py       (NEW)
```

**Reused without modification:**
- `backtests/router_setup_builder.py` — `build_historical_setup(date)` produces the historical `SPYSetup` list (full-fidelity replay; already used by the WF).
- `data/intraday_data.py` — parquet-cached 5-min bars (full-year 2024 already on disk).
- `signals/intraday_entry_router.py::_passes_entry_tier`, `_assign_dte_buckets` — reused as-is by `route_explain`.
- `backtests/intraday_router_wf.py::_MockBroker` — the backfill uses a fresh per-day mock broker for dedup state, exactly as the WF does.

## Trace Record Schema

`route_explain()` returns a `DecisionTrace` dict:

```python
{
    "passed_tier": bool,                     # _passes_entry_tier(setup)
    "candidate_buckets": ["0DTE", ...],      # _assign_dte_buckets output (post tier, pre dedup)
    "accepted": [                            # buckets that survived dedup → would be traded
        {"dte_bucket": "0DTE"},
    ],
    "rejected": [                            # every blocked candidate + the gate that killed it
        {"dte_bucket": None,      "gate": "tier",  "detail": "conviction=standard < minimum=high"},
        {"dte_bucket": "1-3DTE",  "gate": "dte",   "detail": "Friday-PM safeguard: 1-3DTE dropped"},
        {"dte_bucket": "0DTE",    "gate": "dedup", "detail": "open position already in (iron_condor, 0DTE)"},
    ],
}
```

The flattened JSONL row (`build_trace_record`), one object per `(setup, evaluation)`:

```python
{
    "ts":         "2026-05-30T09:16:00-04:00",   # ISO8601, tz-aware US/Eastern
    "date":       "2026-05-30",
    "source":     "live",                         # "live" | "backfill"
    "strategy":   "iron_condor",
    "conviction": "high",
    "score":      72,
    "direction":  "neutral",                      # may be None
    "trend":      "range-bound",                  # SPYSetup.trend proxy; may be None
    "passed_tier": True,
    "accepted":   ["0DTE"],                        # list[str] of dte_buckets
    "rejected":   [                                # list of {dte_bucket, gate, detail}
        {"dte_bucket": "1-3DTE", "gate": "dedup", "detail": "..."},
    ],
}
```

**Gate vocabulary (closed set):** `"tier"`, `"dte"`, `"dedup"` — exactly the three gates in `route()`'s pipeline.

**Parquet columns** (rollup): `ts, date, source, strategy, conviction, score, direction, trend, passed_tier, n_accepted, n_rejected, reject_gates`. The nested `accepted`/`rejected` lists are flattened to counts plus `reject_gates` (a sorted, comma-joined string of the distinct gates that fired, e.g. `"dedup,dte"`) so the parquet stays columnar; the full JSONL remains the lossless source of truth.

## `route_explain()` Reference Implementation

```python
def route_explain(setup, now: datetime, broker) -> dict:
    """Non-mutating decision trace for `route()`'s three-gate pipeline.

    Returns {passed_tier, candidate_buckets, accepted, rejected}. Reuses the
    same gate helpers route() uses, so the accept set is guaranteed identical
    to route()'s output (verified by test).
    """
    rejected: list[dict] = []

    # Gate 1: entry tier.
    if not _passes_entry_tier(setup):
        return {
            "passed_tier": False,
            "candidate_buckets": [],
            "accepted": [],
            "rejected": [{
                "dte_bucket": None,
                "gate": "tier",
                "detail": f"conviction={setup.conviction} < minimum={config.ENTRY_TIER_MINIMUM}",
            }],
        }

    # Gate 2: DTE assignment. Reasons inferred from the universe NOT assigned.
    candidate_buckets = _assign_dte_buckets(setup, now)
    universe = {"0DTE", "1-3DTE"}
    for b in sorted(universe - set(candidate_buckets)):
        rejected.append({
            "dte_bucket": b,
            "gate": "dte",
            "detail": _dte_reject_detail(setup, now, b),
        })

    # Gate 3: dedup. Shared primitive returns allowed + reasons.
    allowed, dedup_rejects = _dedup_partition(setup.strategy, candidate_buckets, broker)
    for b, reason in dedup_rejects:
        rejected.append({"dte_bucket": b, "gate": "dedup", "detail": reason})

    return {
        "passed_tier": True,
        "candidate_buckets": candidate_buckets,
        "accepted": [{"dte_bucket": b} for b in allowed],
        "rejected": rejected,
    }
```

Supporting helpers (new, in the same module):

```python
def _dedup_partition(strategy: str, buckets: list[str], broker
                     ) -> tuple[list[str], list[tuple[str, str]]]:
    """Single dedup implementation. Returns (allowed, [(bucket, reason), ...]).
    _dedup_filter is refactored to call this and return only `allowed`, so
    route()'s observable behavior is unchanged."""
    allowed, rejected = [], []
    for bucket in buckets:
        open_in_combo = [
            t for t in broker.trades.get_trades_by(strategy=strategy, dte_bucket=bucket)
            if t.get("outcome") == "open"
        ]
        if open_in_combo:
            rejected.append((bucket, f"open position already in ({strategy}, {bucket})"))
            continue
        n_today = broker._entry_count_today_by_combo(strategy, bucket)
        if n_today >= config.INTRADAY_PER_COMBO_DAILY_CAP:
            rejected.append((bucket,
                f"per-combo daily cap reached ({n_today} >= {config.INTRADAY_PER_COMBO_DAILY_CAP})"))
            continue
        allowed.append(bucket)
    return allowed, rejected


def _dedup_filter(strategy: str, buckets: list[str], broker) -> list[str]:
    """Thin wrapper over _dedup_partition — route() keeps calling this and gets
    the identical list it got before the refactor."""
    allowed, _ = _dedup_partition(strategy, buckets, broker)
    return allowed


def _dte_reject_detail(setup, now: datetime, bucket: str) -> str:
    """Human-readable reason a bucket was not assigned by _assign_dte_buckets."""
    is_friday = now.weekday() == 4
    cutoff_h, cutoff_m = (int(x) for x in config.INTRADAY_DTE_MORNING_CUTOFF.split(":"))
    is_afternoon = now.time() >= time(cutoff_h, cutoff_m)
    if is_friday and is_afternoon and bucket == "1-3DTE":
        return "Friday-PM safeguard: 1-3DTE dropped (no weekend exposure)"
    if bucket == "0DTE":
        return "afternoon → 1-3DTE assigned, 0DTE not selected"
    return "morning → 0DTE assigned, 1-3DTE not selected"
```

**Critical invariant (tested):** `[a["dte_bucket"] for a in route_explain(s, now, b)["accepted"]] == [d["dte_bucket"] for d in route(s, now, b)]` for the same inputs. `route_explain` must never disagree with `route` on the accept set.

## Live Integration (paper_broker seam)

`learning/paper_broker.py::_phase3_route_impl` is the single live `route()` call site. Modify it to route via `route_explain`, write the trace, and return the accepted `setup_dict`s — preserving the existing return contract:

```python
def _phase3_route_impl(self, setup, now, broker):
    """Route via route_explain so the decision (incl. rejections) is tracked,
    then return the accepted setup_dicts route() would have returned."""
    from signals.intraday_entry_router import route_explain, _build_setup_dict
    trace = route_explain(setup, now, broker)
    try:
        from learning.router_tracker import build_trace_record, write_trace
        write_trace(build_trace_record(setup, trace, now, source="live"))
    except Exception as e:           # Standing Rule #10 — never block execution
        logger.warning(f"router_tracker: trace write failed: {e}")
    return [_build_setup_dict(setup, a["dte_bucket"], now) for a in trace["accepted"]]
```

`_build_setup_dict` is already defined in `intraday_entry_router.py` (used by `route()`); exposing it for reuse keeps the accepted-path construction DRY. The accepted list is built from the trace, so the live behavior is identical to calling `route()`.

## Data Flow

**Live (per trading day, 09:16 ET, inside the paper-broker job):**
```
plan → _build_setups_from_plan → for each setup:
    trace = route_explain(setup, 09:16_ET, self)
    write_trace(build_trace_record(setup, trace, now, "live"))   # append JSONL
    accepted setup_dicts → execute_signal (unchanged)
```

**Backfill (offline, one-shot):**
```
for day in trading_days(start, end):
    setups = build_historical_setup(day)            # router_setup_builder (09:45 ET window)
    broker = _MockBroker()                           # fresh per day
    ts = 09:45 ET on `day`
    for setup in setups:
        trace = route_explain(setup, ts, broker)
        write_trace(build_trace_record(setup, trace, ts, "backfill"), day=day)
        for a in trace["accepted"]:                  # mirror dedup state forward
            broker.record_open(strategy=setup.strategy, dte_bucket=a["dte_bucket"])
```

**Rollup (offline, on-demand before analysis):**
```
records = load_jsonl_dir("logs/learning/router_track/")
rollup_to_parquet(records, "backtests/.cache/router_track/router_track.parquet")
```

All datetimes are `pytz.timezone("US/Eastern")`, matching every other module.

## Error Handling

| Failure | Where | Behavior |
|---|---|---|
| Trace write fails (disk, serialization) | `_phase3_route_impl` | Caught; `logger.warning`; paper execution proceeds with accepted setups. Standing Rule #10. |
| `route_explain` raises | `_phase3_route_impl` | NOT caught here — a routing bug should surface, same as `route()` raising today. (The try/except wraps only the *tracking write*, not the routing.) |
| JSONL file missing on rollup | `load_jsonl_dir` | Empty dir → empty list → rollup writes an empty (schema-only) parquet; logged. |
| Corrupt JSONL line | `load_jsonl_dir` | Skip the line, `logger.warning` with line number; never abort the whole rollup. |
| Backfill day has no intraday data | `build_historical_setup` returns `[]` | Day yields zero records; not an error. |
| Backfill insufficient daily history (<30 bars) | `build_historical_setup` raises `ValueError` | Caught per-day, logged, skipped (only at the very start of the range). |
| Cross-day dedup leak in backfill | `_MockBroker` fresh per day | Per-day instance; impossible to leak by construction. Tested. |

**Logging:** loguru. Live: one `DEBUG` per setup tracked. Backfill/rollup: one `INFO` summary line (n days, n records, output path).

## Testing

```
tests/test_route_explain.py
  ✓ accept-set parity: route_explain accepted == route output, across (tier-fail, friday-pm,
      ultra-conviction-double, dedup-blocked, all-pass) fixtures
  ✓ tier fail → single rejected{gate:"tier"}, accepted == []
  ✓ friday-pm afternoon → rejected contains {dte_bucket:"1-3DTE", gate:"dte"}
  ✓ dedup blocked (open position in combo) → rejected{gate:"dedup"}, detail names the combo
  ✓ dedup blocked (daily cap) → rejected{gate:"dedup"}, detail names the cap
  ✓ all-pass ultra-conviction → accepted has both buckets, rejected == []
  ✓ _dedup_filter still returns the same list as before (wrapper-over-partition regression)

tests/test_router_tracker.py
  ✓ build_trace_record flattens DecisionTrace → expected JSONL row (keys, types, tz-aware ts)
  ✓ build_trace_record handles direction=None and trend=None without KeyError
  ✓ write_trace appends one line per call; file path is logs/learning/router_track/<date>.jsonl
  ✓ write_trace round-trips: written line json.loads back to the input record

tests/test_router_track_backfill.py
  ✓ backfill over a 2-day stub range (monkeypatched build_historical_setup) writes N records
  ✓ fresh MockBroker per day: a dedup-blocking open on day 1 does NOT block day 2
  ✓ accepted opens are recorded forward so a second same-combo setup same day hits dedup
  ✓ day with empty setups yields zero records, no crash

tests/test_router_track_rollup.py
  ✓ load_jsonl_dir reads multiple files, skips corrupt lines (logs, no abort)
  ✓ rollup_to_parquet emits expected columns incl. n_accepted, n_rejected, reject_gates
  ✓ reject_gates is sorted-distinct comma-joined (e.g. "dedup,dte")
  ✓ empty dir → schema-only parquet, no crash
```

**Integration test** (`@pytest.mark.integration`, excluded from default run): `backfill("2024-04-01", "2024-04-05")` against real cached parquet → non-empty JSONL → rollup → parquet with ≥1 row. Confirms the end-to-end path on real data.

Per Standing Rule #4 every new module gets a test file; per Rule #5 run `pytest tests/ -v -m "not integration" --tb=short` before each commit.

## Out of Scope / Future Work

- **Multi-tick replay** — the conditional step 3. `route_explain` is timestamp-parameterized, so multi-tick is a loop over intraday timestamps feeding the same backfill writer. Triggered only if calibration (spec 2026-05-29) returns "shelve" — see that spec's §"If Calibration Says 'Shelve' — Investigate Multi-Tick First".
- **Regime enrichment** — add a real `regime` column once a regime value is available at routing time (would also unblock the calibration spec's deferred regime-conditional thresholds).
- **Reflector consumption** — a follow-up wires `learning/reflector.py` to summarize the day's `router_track` JSONL into the nightly narrative + KB entries (the autonomous-loop payoff). This spec only produces the data; reading it is a separate, small change.
- **Web-app surfacing** — the dashboard reading `router_track.parquet` for a gating-distribution view.
- **Scheduled rollup** — if analysis becomes routine, promote the on-demand rollup CLI to a weekly APScheduler job (wrapped per Rule #10).
```
