# Brainstorm handoff — Intraday router tracking surface

_Status: brainstorming in progress (superpowers:brainstorming). Written 2026-05-30 before context wrap._

## The plan we locked (sequencing)
1. **Tracking surface now** — capture what the intraday router *would have decided*, bar-by-bar.
2. **Calibration next** — use the captured record to tune the router WF thresholds (see spec from commit e9df317 "router WF threshold calibration").
3. **Multi-tick replay only if needed** — conditional, not committed. (Commit a921503 flags multi-tick replay as required investigation before shelving.)

## Key finding: there is no "tick" data — it's 5-minute bars
- Intraday data lives in **`backtests/.cache/`** as per-day parquet, written/read by **`data/intraday_data.py::get_stock_intraday`** (cache-first; uses Polygon `list_aggs` so whole ranges paginate, unlike `polygon_client.get_bars` which truncates ~50k).
- Already on disk: **252 files `SPY_5minute_*.parquet`, full-year 2024 (2024-01-02 → 2024-12-31)** + an `options/` subdir (real option aggregates via `data/options_history.py`).
- So **"multi-tick replay" = replaying cached 5-min bars through the router.** No new ingestion/storage layer needed. Data layer already exists and is populated.
- Consistent with `[[project_intraday_touch_shelved]]`: "option B (5-min bars) is the warranted next step."

## Decision made this session
**Where to write the tracking record: BOTH — JSONL live, parquet rollup.**
- Live: `logs/learning/router_track/<date>.jsonl` (so the 19:00 ET reflector can narrate it — fits the autonomous self-learning loop).
- Rollup step compacts to `backtests/.cache/router_track/*.parquet` for the calibration walk-forward harness (`backtests/intraday_router_wf.py`).
- Both consumers fed: reflector (nightly narrative) + walk-forward (calibration).

## OPEN — next questions to resolve (where we stopped)
1. **Record schema.** Need to read `signals/intraday_entry_router.py` (the router's decision output) and `backtests/intraday_router_wf.py` (what calibration consumes) to define columns. Draft from earlier preview:
   `{ts, bar, regime, setup, would_enter, score, rr, gate_blocks[]}`. Confirm against actual router return shape + gate names (`signals/gates.py`).
2. **When does the live tracker run?** Piggyback on existing `scanners/intraday_scanner.py` (every 5 min, 9:30–16:00 ET) vs a dedicated job. Respect Standing Rules #1/#2 (no weekends, intraday only 9:30–16:00).
3. **Rollup trigger.** New scheduler job vs end-of-day hook vs on-demand before a calibration run.
4. **Backfill.** Do we replay the 252 cached 2024 days through the router once to seed the parquet (gives calibration a full year immediately), or only accumulate live going forward? Backfill is the fast path to calibration.

## Files to read first next session
- `signals/intraday_entry_router.py` — router decision output (was mid-read when context ran out)
- `backtests/intraday_router_wf.py` — calibration harness, what it consumes
- `signals/gates.py` — gate names for `gate_blocks[]`
- `scanners/intraday_scanner.py` — candidate host for the live tracker
- `learning/reflector.py` — how nightly narration reads logs
- `backtests/wf_common.py` (already read): `split_oos` (60/40 IS/OOS), `metrics_block` ({trades,win_rate,pnl,sharpe})

## Related memory
`[[project_intraday_touch_shelved]]`, `[[feedback_walk_forward_honest_ml]]` (OOS-first, fewer knobs, shelve if no edge), `[[reference_intraday_data_tiers]]` (PAID Polygon: ~2yr intraday + real option aggregates).
