# Brainstorm handoff — Intraday router tracking surface

_Status: brainstorming COMPLETE → design spec written. See `docs/superpowers/specs/2026-05-30-router-explain-tracking-surface-design.md`. Written 2026-05-30._

> **Superseded by the spec.** This file is the brainstorm trail; the spec is the authoritative design. All four OPEN items resolved there as Locked Decisions (regime omitted/`trend` proxy; rollup = on-demand CLI; backfill required, seeds 2024).
>
> **Correction (important):** `route()` is NOT wired into the live path. The only live importer is `scanners/intraday_scanner.py:40` and that import is *dangling* (never called); `paper_broker.py` does not touch the router. So the spec is two-phase: **Phase 1** = offline `route_explain()` + backfill + rollup (unblocks calibration, no live change); **Phase 2** = activate `route()` at the scanner seam (`_scan_spy_intraday`, ~line 169, where a code comment already says "router will gate here once live") + live JSONL tracker, which requires threading the live `PaperBroker` into the scanner for the dedup gate. Phase 2 is a real behavior change and should land after the calibration verdict. User direction 2026-05-30: build both, Phase 1 first.

## The plan we locked (sequencing)
1. **Tracking surface now** — capture what the intraday router *would have decided*, bar-by-bar.
2. **Calibration next** — use the captured record to tune the router WF thresholds (see spec from commit e9df317 "router WF threshold calibration").
3. **Multi-tick replay only if needed** — conditional, not committed. (Commit a921503 flags multi-tick replay as required investigation before shelving.)

## Key finding: there is no "tick" data — it's 5-minute bars
- Intraday data lives in **`backtests/.cache/`** as per-day parquet, written/read by **`data/intraday_data.py::get_stock_intraday`** (cache-first; uses Polygon `list_aggs` so whole ranges paginate, unlike `polygon_client.get_bars` which truncates ~50k).
- Already on disk: **252 files `SPY_5minute_*.parquet`, full-year 2024 (2024-01-02 → 2024-12-31)** + an `options/` subdir (real option aggregates via `data/options_history.py`).
- So **"multi-tick replay" = replaying cached 5-min bars through the router.** No new ingestion/storage layer needed. Data layer already exists and is populated.
- Consistent with `[[project_intraday_touch_shelved]]`: "option B (5-min bars) is the warranted next step."

## Decisions made this session
**1. Where to write the tracking record: BOTH — JSONL live, parquet rollup.**
- Live: `logs/learning/router_track/<date>.jsonl` (so the 19:00 ET reflector can narrate it — fits the autonomous self-learning loop).
- Rollup step compacts to `backtests/.cache/router_track/*.parquet` for the calibration walk-forward harness (`backtests/intraday_router_wf.py`).
- Both consumers fed: reflector (nightly narrative) + walk-forward (calibration).

**2. How rejection reasons get out: a sibling `route_explain()` function.**
- `signals/intraday_entry_router.route()` stays UNTOUCHED (returns `[setup_dict]`, hot path untouched).
- New `route_explain(setup, now, broker) -> {accepted:[...], rejected:[{bucket, gate, detail}], ...}` returns the full per-candidate decision trace.
- Both the live tracker and the WF derive the accepted set from `explain()`; the tracker writes the trace.
- Build with TDD (Standing Rule #4: new module needs a test file).

## What the code actually does (confirmed by reading)
- `route()` applies three gates IN ORDER and discards rejection reasons: (1) `_passes_entry_tier` (conviction vs `config.ENTRY_TIER_MINIMUM`, ranks watch<standard<high), (2) `_assign_dte_buckets` (H2: morning→0DTE / afternoon→1-3DTE, Friday-PM safeguard→0DTE only, ultra-conviction `score>=ULTRA_CONVICTION_DOUBLE_DTE_SCORE`→both), (3) `_dedup_filter` (one open per (strategy,bucket) + `INTRADAY_PER_COMBO_DAILY_CAP`/day). These three are the `gate` values for `rejected[].gate`.
- The WF (`intraday_router_wf.run_window`) calls `route()` ONCE per day at a fixed **9:45 ET** timestamp — single opening-range entry, NOT bar-by-bar. So today there is no intraday re-evaluation at all.
- **Calibration = pick honest values for 4 currently-`None` thresholds** in `intraday_router_wf.py:334-337`: `MIN_DELTA_PNL_PER_TRADE`, `MIN_OOS_PNL`, `MIN_OOS_SHARPE`, `MIN_OOS_WIN_RATE`. While all None, `window_verdict()` returns `"raw"`. Treatment=tier-gate-on vs Baseline=tier-gate-off (`_bypass_tier_gate`), apples-to-apples per day.
- **Multi-tick replay (the conditional step 3) precisely means:** call `route_explain()` at MULTIPLE intraday bars through the day instead of only 9:45 — i.e. would a setup that's blocked/absent at 9:45 become tradeable at 10:30, 13:00, etc. The 5-min parquets already support this with zero new data work.

## OPEN — next questions to resolve (where we stopped)
1. **`route_explain()` return schema + record columns.** Gate names confirmed: `tier`, `dte`, `dedup`. Draft trace record:
   `{ts, regime, strategy, conviction, score, direction, accepted:[bucket...], rejected:[{bucket, gate, detail}]}`. Confirm `regime` source (SPYSetup field?) before finalizing.
2. **When does the live tracker run?** Piggyback on existing `scanners/intraday_scanner.py` (every 5 min, 9:30–16:00 ET) vs a dedicated job. Respect Standing Rules #1/#2 (no weekends, intraday only 9:30–16:00). NOTE: live tracker at every bar is ALSO the natural place that makes multi-tick replay trivial later.
3. **Rollup trigger.** New scheduler job vs end-of-day hook vs on-demand before a calibration run.
4. **Backfill.** Replay the 252 cached 2024 days through `route_explain()` once to seed the parquet (gives calibration a full year immediately) vs accumulate live only. Backfill is the fast path to calibration — and the calibration step NEEDS history, so backfill is effectively required for step 2.

## Files to read first next session
- `signals/intraday_entry_router.py` — router decision output (was mid-read when context ran out)
- `backtests/intraday_router_wf.py` — calibration harness, what it consumes
- `signals/gates.py` — gate names for `gate_blocks[]`
- `scanners/intraday_scanner.py` — candidate host for the live tracker
- `learning/reflector.py` — how nightly narration reads logs
- `backtests/wf_common.py` (already read): `split_oos` (60/40 IS/OOS), `metrics_block` ({trades,win_rate,pnl,sharpe})

## Related memory
`[[project_intraday_touch_shelved]]`, `[[feedback_walk_forward_honest_ml]]` (OOS-first, fewer knobs, shelve if no edge), `[[reference_intraday_data_tiers]]` (PAID Polygon: ~2yr intraday + real option aggregates).
