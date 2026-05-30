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

This spec adds a **decision tracking surface** in two phases: a non-mutating `route_explain()` plus an offline backfill/rollup that feeds calibration (Phase 1, zero live behavior change), then live activation of the router in the intraday scanner with a live JSONL tracker at that seam (Phase 2).

This is sequencing step 1 of the locked plan: **tracking surface now → calibration (spec 2026-05-29) → multi-tick replay only if needed** (per commit a921503; see calibration spec §"If Calibration Says 'Shelve' — Investigate Multi-Tick First").

### Pre-existing live wiring (verified this session)

**`route()` IS wired into the live path — behind a feature flag that is off by default.** The live seam is `scanners/intraday_scanner.py::_scan_spy_intraday`, lines 200-223:

```python
# scanners/intraday_scanner.py:203-217 (existing)
if not config.INTRADAY_PAPER_BROKER_ENABLED:        # defaults False
    continue
now_et = datetime.now(EASTERN)
try:
    broker      = PaperBroker()                      # broker already in scope
    setup_dicts = _route_entry(setup, now_et, broker)   # ← route() called here (line 211)
    for sd in setup_dicts:
        result = broker.execute_signal(sd)
        ...
except Exception as e:                               # already isolated (Rule #10)
    logger.exception(...)
```

`config.INTRADAY_PAPER_BROKER_ENABLED` defaults to `false` ("router live only when explicitly enabled"). So the router runs live only when an operator sets the env flag; otherwise the block is skipped. `learning/paper_broker.py` does not call the router — the scanner is the sole live caller; the backtest (`intraday_router_wf.py`) is the only other caller.

Consequences:
- **Calibration needs the offline backfill regardless** (it replays historical 2024, independent of any live flag). Phase 1 fully unblocks it.
- **The live tracker hooks an existing, complete seam** — the broker is already constructed (line 210) and the call is already exception-isolated (line 218). Phase 2 is therefore *additive observability at line 211*, not new wiring: swap `_route_entry` → `route_explain`, write the trace, execute the same accepted set. It emits live data only while the flag is on, so by default it changes nothing.

## Phasing

| Phase | Scope | Live behavior change? | Unblocks |
|---|---|---|---|
| **Phase 1** | `route_explain()` + offline backfill over cached 2024 + parquet rollup | No | Calibration (spec 2026-05-29) |
| **Phase 2** (deferred) | Add `route_explain` + live trace write at the existing scanner seam (line 211) | No (additive; emits data only while `INTRADAY_PAPER_BROKER_ENABLED` is on) | Live gating-behavior data; nightly reflector narration |

**User direction (2026-05-30): Offline-only v1 — build Phase 1 now, defer Phase 2.** Phase 1 unblocks calibration with zero live change. Phase 2 is documented here for completeness but not built in this pass; when built, it is purely additive observability at the existing seam (the tracker records the routing *decision*, never executes differently), and produces live rows only when the operator has explicitly enabled the router flag.

## Goals

- Add `signals/intraday_entry_router.route_explain(setup, now, broker)` — a pure function returning the full decision trace (accepted buckets + rejected buckets with the gate that blocked each), **without changing `route()`'s observable behavior**.
- Backfill the trace over the 252 cached 2024 trading days (offline replay through `route_explain()`), and roll it up to a single parquet for calibration / analysis.
- (Phase 2) Activate the router in `scanners/intraday_scanner.py` and capture each live scan's routing decision, with rejection reasons, as append-only JSONL under `logs/learning/` so the 19:00 ET reflector can read and narrate it.
- Stay DRY: `route_explain()` reuses the existing gate helpers; no gate logic is duplicated.

## Non-Goals (deferred)

- **Multi-tick replay** — `route_explain()` is evaluated at a single decision point per scan. Re-evaluating an entire day's 5-min bars in the *backtest* is the explicit *conditional* step 3, gated on the calibration verdict. The surface is designed so multi-tick becomes a loop over timestamps later; v1 does not add it.
- **Regime in the trace record** — `SPYSetup` carries no `regime` field (confirmed: it has `trend`, `score`, `conviction`, `direction`, not regime). The record stores `trend` as a lightweight proxy. Regime-conditional analysis is deferred, matching the calibration spec's deferral of regime-conditional thresholds.
- **Changing what the router accepts** — pure observability of the reasons; `route()`'s accept/reject logic is unchanged.
- **Phase 4b structure builder / real pricing** — the live router still emits placeholder pricing. Out of scope; does not affect the tracking decision data.
- **Reflector wiring** — Phase 2 produces the JSONL; teaching `reflector.py` to summarize it is a small follow-up, not this spec.
- **Web-app surfacing** — the dashboard reading the parquet is later.
- **Promoting/raising any cap** — `MAX_CONCURRENT_DISCIPLINED` etc. unchanged.

## Locked Decisions

| Decision | Choice | Source |
|---|---|---|
| How rejection reasons get out | New sibling `route_explain()`; `route()` behaviorally untouched | Brainstorm: "Sibling explain() function" |
| Record written as | JSONL (append-only) + parquet rollup | Brainstorm: "Both — JSONL now, parquet rollup" |
| Live JSONL location | `logs/learning/router_track/YYYY-MM-DD.jsonl` | `logs/learning/` is the reflector's read root |
| Rollup parquet location | `backtests/.cache/router_track/router_track.parquet` | Alongside the cached bars the analysis reads |
| Phase 1 backfill source | `backtests/router_setup_builder.build_historical_setup` → `route_explain` over a date range | Reuses the WF's full-fidelity historical SPYSetup factory |
| Phase 1 backfill eval time | 09:45 ET post-OR (matches the WF's tick-of-day) | Apples-to-apples with `intraday_router_wf` |
| Phase 2 live seam (deferred) | `scanners/intraday_scanner.py:211` — swap `_route_entry` → `route_explain`, write trace, execute accepted | Existing live seam; broker already at line 210, call already exception-isolated at line 218 |
| Phase 2 live cadence | Every 5 min during market hours, only while `INTRADAY_PAPER_BROKER_ENABLED` is on | Scanner's existing cadence + existing feature flag (default off) |
| Rollup trigger | On-demand CLI (no auto-schedule in v1) | Honest-ML discipline: analysis is a deliberate step |
| Regime in record | Omitted; store `trend` proxy | `SPYSetup` has no regime field |
| DRY for dedup reasons | Extract `_dedup_partition()`; `_dedup_filter()` becomes a thin wrapper over it | One dedup implementation; `route()` output identical |
| Failure isolation | Tracker write wrapped in try/except at the live seam | Standing Rule #10 — a tracker failure must never break scanning |

## Architecture

```
signals/intraday_entry_router.py        (MODIFY — additive, Phase 1)
    ├── _dedup_partition(strategy, buckets, broker)
    │       → (allowed: list[str], rejected: list[tuple[str, str]])   # NEW shared primitive
    ├── _dedup_filter(strategy, buckets, broker)                       # REFACTOR → wrapper over _dedup_partition
    ├── _dte_reject_detail(setup, now, bucket) → str                  # NEW reason helper
    ├── route(setup, now, broker)                                      # UNCHANGED
    └── route_explain(setup, now, broker) → DecisionTrace (dict)       # NEW pure function

learning/router_tracker.py              (NEW — Phase 1)
    ├── build_trace_record(setup, trace, now, source) → dict          # flatten DecisionTrace → JSONL row
    └── write_trace(record, day=None) → str                           # append logs/learning/router_track/<day>.jsonl

backtests/router_track_backfill.py      (NEW — Phase 1)
    └── backfill(start, end) → int                                    # replay build_historical_setup → route_explain
        if __name__ == "__main__": CLI

backtests/router_track_rollup.py        (NEW — Phase 1)
    ├── load_jsonl_dir(dir) → list[dict]
    ├── rollup_to_parquet(records, out_path) → str
    └── if __name__ == "__main__": CLI

scanners/intraday_scanner.py            (MODIFY — Phase 2, DEFERRED)
    └── _scan_spy_intraday():211 swap _route_entry → route_explain in the
        existing flag-gated block; write live trace; execute the same accepted set

tests/test_route_explain.py             (NEW — Phase 1)
tests/test_router_tracker.py            (NEW — Phase 1)
tests/test_router_track_backfill.py     (NEW — Phase 1)
tests/test_router_track_rollup.py       (NEW — Phase 1)
tests/test_intraday_scanner_routing.py  (NEW — Phase 2)
```

**Reused without modification:** `backtests/router_setup_builder.py` (`build_historical_setup`), `data/intraday_data.py` (cached 5-min bars), `intraday_entry_router._passes_entry_tier`/`_assign_dte_buckets`, `backtests/intraday_router_wf.py::_MockBroker` (backfill's per-day dedup broker).

## Trace Record Schema

`route_explain()` returns a `DecisionTrace` dict:

```python
{
    "passed_tier": bool,                     # _passes_entry_tier(setup)
    "candidate_buckets": ["0DTE", ...],      # _assign_dte_buckets output (post tier, pre dedup)
    "accepted": [{"dte_bucket": "0DTE"}],    # buckets that survived dedup → would be traded
    "rejected": [                            # every blocked candidate + the gate that killed it
        {"dte_bucket": None,     "gate": "tier",  "detail": "conviction=standard < minimum=high"},
        {"dte_bucket": "1-3DTE", "gate": "dte",   "detail": "Friday-PM safeguard: 1-3DTE dropped"},
        {"dte_bucket": "0DTE",   "gate": "dedup", "detail": "open position already in (iron_condor, 0DTE)"},
    ],
}
```

Flattened JSONL row (`build_trace_record`), one object per `(setup, evaluation)`:

```python
{
    "ts":          "2026-05-30T10:35:00-04:00",  # ISO8601, tz-aware US/Eastern
    "date":        "2026-05-30",
    "source":      "live",                        # "live" | "backfill"
    "strategy":    "iron_condor",
    "conviction":  "high",
    "score":       72,
    "direction":   "neutral",                     # may be None
    "trend":       "range-bound",                 # SPYSetup.trend proxy; may be None
    "passed_tier": True,
    "accepted":    ["0DTE"],                       # list[str] of dte_buckets
    "rejected":    [{"dte_bucket": "1-3DTE", "gate": "dedup", "detail": "..."}],
}
```

**Gate vocabulary (closed set):** `"tier"`, `"dte"`, `"dedup"`.

**Parquet columns** (rollup): `ts, date, source, strategy, conviction, score, direction, trend, passed_tier, n_accepted, n_rejected, reject_gates`. Nested lists flatten to counts plus `reject_gates` (sorted, comma-joined distinct gates, e.g. `"dedup,dte"`); the JSONL remains the lossless source of truth.

## `route_explain()` Reference Implementation (Phase 1)

```python
def route_explain(setup, now: datetime, broker) -> dict:
    """Non-mutating decision trace for route()'s three-gate pipeline.
    Reuses the same gate helpers route() uses, so the accept set is identical
    to route()'s output (verified by test)."""
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
        rejected.append({"dte_bucket": b, "gate": "dte",
                         "detail": _dte_reject_detail(setup, now, b)})

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

Supporting helpers (new, same module):

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

**Critical invariant (tested):** for the same inputs,
`[a["dte_bucket"] for a in route_explain(s, now, b)["accepted"]] == [d["dte_bucket"] for d in route(s, now, b)]`.
`route_explain` must never disagree with `route` on the accept set.

## Phase 1 — Offline backfill + rollup

**Backfill** (`backtests/router_track_backfill.py`) replays cached history through `route_explain`, mirroring the WF's per-day, fresh-`_MockBroker`, 09:45-ET model:

```python
def backfill(start: date, end: date) -> int:
    """Replay [start, end] through route_explain; append trace rows
    (source='backfill') to logs/learning/router_track/<day>.jsonl. Returns
    the number of rows written."""
    from backtests.router_setup_builder import build_historical_setup
    from backtests.intraday_router_wf import _MockBroker
    from learning.router_tracker import build_trace_record, write_trace
    from signals.intraday_entry_router import route_explain
    import pytz
    et = pytz.timezone("US/Eastern")

    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:                       # Standing Rule #1: weekdays only
            try:
                setups = build_historical_setup(d)
            except ValueError as e:               # insufficient daily history at range start
                logger.debug(f"backfill: skip {d}: {e}")
                setups = []
            broker = _MockBroker()                 # fresh per day — no cross-day leak
            ts = et.localize(datetime.combine(d, time(9, 45)))
            for setup in setups:
                trace = route_explain(setup, ts, broker)
                write_trace(build_trace_record(setup, trace, ts, "backfill"), day=d)
                n += 1
                for a in trace["accepted"]:        # mirror dedup state forward within the day
                    broker.record_open(strategy=setup.strategy, dte_bucket=a["dte_bucket"])
        d += timedelta(days=1)
    logger.info(f"backfill: {n} trace rows over {start}..{end}")
    return n
```

**Rollup** (`backtests/router_track_rollup.py`) compacts all JSONL → parquet:

```python
def rollup_to_parquet(records: list[dict], out_path: str) -> str:
    import pandas as pd, os
    rows = [{
        "ts": r["ts"], "date": r["date"], "source": r["source"],
        "strategy": r["strategy"], "conviction": r["conviction"],
        "score": r["score"], "direction": r.get("direction"),
        "trend": r.get("trend"), "passed_tier": r["passed_tier"],
        "n_accepted": len(r["accepted"]),
        "n_rejected": len(r["rejected"]),
        "reject_gates": ",".join(sorted({x["gate"] for x in r["rejected"]})),
    } for r in records]
    df = pd.DataFrame(rows, columns=[
        "ts", "date", "source", "strategy", "conviction", "score",
        "direction", "trend", "passed_tier", "n_accepted", "n_rejected", "reject_gates"])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_parquet(out_path)
    return out_path
```

## Phase 2 — Live tracker at the existing seam (DEFERRED per user)

> Not built in this pass. Documented so the follow-up is unambiguous. The alert path (`_scan_spy_intraday` lines 174-198) is untouched — Phase 2 only augments the flag-gated paper-broker block (lines 200-223).

The seam already exists and is complete. The change is to route via `route_explain` (which returns the same accept set, by the tested invariant), write the trace, then build and execute the accepted `setup_dict`s exactly as today:

```python
# scanners/intraday_scanner.py:209-217 — Phase 2 edit (flag-gated block only)
from signals.intraday_entry_router import route_explain, _build_setup_dict
from learning.router_tracker import build_trace_record, write_trace
now_et = datetime.now(EASTERN)
try:
    broker = PaperBroker()                               # unchanged (line 210)
    trace  = route_explain(setup, now_et, broker)        # was: _route_entry(...)
    try:
        write_trace(build_trace_record(setup, trace, now_et, source="live"))
    except Exception as e:                               # Standing Rule #10
        logger.warning(f"router_tracker: live trace write failed: {e}")
    for a in trace["accepted"]:                          # same accepted set as route()
        sd = _build_setup_dict(setup, a["dte_bucket"], now_et)
        result = broker.execute_signal(sd)
        logger.info(f"Phase 3 entry: {sd['strategy']} @ {sd['dte_bucket']} → "
                    f"trade_id={result.get('trade_id')} recorded={result.get('recorded')}")
except Exception as e:                                   # existing outer guard (line 218)
    logger.exception(f"Phase 3 entry pipeline error for {setup.strategy}: {e}")
```

**No new dependency, no behavior change.** The broker is already constructed (line 210); the accepted set executed is identical to today's `_route_entry` output (accept-set invariant); the only addition is the trace write, itself wrapped in try/except. Live rows are produced only while `INTRADAY_PAPER_BROKER_ENABLED` is on (default off), so enabling the tracker by default changes nothing. `_build_setup_dict` is already defined in `intraday_entry_router.py`; expose it for reuse.

## Data Flow

**Phase 1 backfill (offline, one-shot):** `for each weekday in range → build_historical_setup (09:45 ET window) → fresh _MockBroker → route_explain per setup → write JSONL(source=backfill) → record_open accepted forward`.

**Phase 1 rollup (offline, on-demand):** `load_jsonl_dir("logs/learning/router_track/") → rollup_to_parquet → backtests/.cache/router_track/router_track.parquet`.

**Phase 2 live (every 5 min, market hours; only while `INTRADAY_PAPER_BROKER_ENABLED` is on):** alert path unchanged → flag-gated block: `route_explain(setup, now, broker) → write JSONL(source=live) → execute accepted via broker.execute_signal` (same accepted set as today's `_route_entry`).

All datetimes `pytz.timezone("US/Eastern")`.

## Error Handling

| Failure | Where | Behavior |
|---|---|---|
| Live trace write fails (disk, serialization) | `_scan_spy_intraday` line ~213 | Inner try/except: `logger.warning`; routing + execution proceed. Standing Rule #10. |
| `route_explain` raises live | `_scan_spy_intraday` | Caught by the existing outer guard (line 218, `logger.exception`); scanner continues to other tickers — identical to today's `_route_entry` failure handling. |
| JSONL dir missing on rollup | `load_jsonl_dir` | Empty list → schema-only parquet; logged. |
| Corrupt JSONL line | `load_jsonl_dir` | Skip line, `logger.warning` with line no.; never abort the rollup. |
| Backfill day has no intraday data | `build_historical_setup` returns `[]` | Zero rows that day; not an error. |
| Backfill insufficient daily history (<30 bars) | `build_historical_setup` raises `ValueError` | Caught per-day, logged, skipped (only at range start). |
| Cross-day dedup leak in backfill | fresh `_MockBroker` per day | Impossible by construction. Tested. |

**Logging:** loguru. Live: one `DEBUG` per setup tracked. Backfill/rollup: one `INFO` summary (n days/rows, output path).

## Testing

```
tests/test_route_explain.py                      (Phase 1)
  ✓ accept-set parity: route_explain accepted == route output across
      (tier-fail, friday-pm, ultra-conviction-double, dedup-blocked, all-pass) fixtures
  ✓ tier fail → single rejected{gate:"tier"}, accepted == []
  ✓ friday-pm afternoon → rejected contains {dte_bucket:"1-3DTE", gate:"dte"}
  ✓ dedup blocked (open position) → rejected{gate:"dedup"}, detail names the combo
  ✓ dedup blocked (daily cap) → rejected{gate:"dedup"}, detail names the cap
  ✓ all-pass ultra-conviction → accepted has both buckets, rejected == []
  ✓ _dedup_filter still returns the same list as before (wrapper-over-partition regression)

tests/test_router_tracker.py                     (Phase 1)
  ✓ build_trace_record flattens DecisionTrace → expected row (keys, types, tz-aware ts)
  ✓ build_trace_record handles direction=None and trend=None without KeyError
  ✓ write_trace appends one line per call to logs/learning/router_track/<date>.jsonl
  ✓ write_trace round-trips: written line json.loads back to the input record

tests/test_router_track_backfill.py              (Phase 1)
  ✓ backfill over a 2-day stub range (monkeypatched build_historical_setup) writes N rows
  ✓ fresh MockBroker per day: a day-1 dedup-blocking open does NOT block day 2
  ✓ accepted opens recorded forward → second same-combo setup same day hits dedup
  ✓ weekend days skipped; empty-setup day yields zero rows, no crash

tests/test_router_track_rollup.py                (Phase 1)
  ✓ load_jsonl_dir reads multiple files, skips corrupt lines (logs, no abort)
  ✓ rollup_to_parquet emits expected columns incl. n_accepted, n_rejected, reject_gates
  ✓ reject_gates is sorted-distinct comma-joined (e.g. "dedup,dte")
  ✓ empty dir → schema-only parquet, no crash

tests/test_intraday_scanner_routing.py           (Phase 2 — DEFERRED)
  ✓ flag off → flag-gated block skipped, no trace written, alert path unchanged
  ✓ flag on + stub broker → route_explain trace written (source="live") for the setup
  ✓ flag on → accepted buckets executed via broker.execute_signal (same set as route())
  ✓ a trace-write exception is swallowed; execution still proceeds (Rule #10)
```

**Integration test** (`@pytest.mark.integration`, excluded from default run): `backfill("2024-04-01", "2024-04-05")` against real cached parquet → non-empty JSONL → rollup → parquet with ≥1 row. Confirms the end-to-end offline path on real data.

Per Standing Rule #4 every new module gets a test file; per Rule #5 run `pytest tests/ -v -m "not integration" --tb=short` before each commit.

## Out of Scope / Future Work

- **Multi-tick replay** — conditional step 3. `route_explain` is timestamp-parameterized, so multi-tick is a loop over intraday timestamps feeding the same backfill writer. Triggered only if calibration (spec 2026-05-29) returns "shelve" — see that spec's §"If Calibration Says 'Shelve' — Investigate Multi-Tick First".
- **Regime enrichment** — add a real `regime` column once a regime value exists at routing time (also unblocks the calibration spec's deferred regime-conditional thresholds).
- **Reflector consumption** — wire `learning/reflector.py` to summarize the day's `router_track` JSONL into the nightly narrative + KB entries (the autonomous-loop payoff).
- **Web-app surfacing** — dashboard reading `router_track.parquet` for a gating-distribution view.
- **Scheduled rollup** — promote the on-demand CLI to a weekly APScheduler job (wrapped per Rule #10) if analysis becomes routine.
