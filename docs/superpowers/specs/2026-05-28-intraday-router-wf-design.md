# Intraday Entry-Router Walk-Forward Backtest — Design Spec

**Date:** 2026-05-28
**Status:** Approved design, pending implementation plan
**Author:** brainstormed with Claude Code

---

## Problem / Motivation

`signals/intraday_entry_router.py` (Phase 3, shipped 2026-05-23) is the bot's first attempt at the **ENTRY-level retune** flagged in the 2026-05-21 0DTE shelving memo. The router gates `intraday_scanner` setups by conviction tier, time-of-day DTE assignment, and per-combo dedup before they reach `paper_broker.execute_signal`.

Live since 2026-05-23, but live performance can't answer the edge question: Phase 3's structures are stub placeholders (synthetic strike-0 legs, $1 entry / $200 max / $100 max-loss), so the journal's PnL is fictional until Phase 4b's structure builder ships. We need a real-priced, walk-forward backtest that isolates the **router's filtering contribution** before we trust it with capital.

The 5/21 0DTE backtest (`backtests/intraday_backtest.py`, no router) lost -$515 with Sharpe -2.23 over full-year 2024 — that's the explicit no-edge baseline. This spec validates whether Phase 3's tier-gate flips that verdict, on the same structures, with proper OOS discipline.

## Goals

- Wrap `backtests/intraday_backtest.py` with `signals/intraday_entry_router.py` and walk-forward the combined system over 2024–2025 with rolling 6mo train / 3mo test windows.
- Run **treatment** (router-gated) and **baseline** (tier-gate disabled) on identical days, identical structures, identical exits — the only delta is which days/buckets the router approves.
- Emit raw per-window stats (Δ$/trade, Δ Sharpe, OOS PnL, win rate, n_trades). Threshold-picking is its own follow-up exercise once we see the numbers.
- Reuse the existing real-priced day simulator without modification — `intraday_backtest.py`, `data/options_history.py`, `data/intraday_data.py`, `signals/spy_options_engine.py` all stay as-is.

## Non-Goals (deferred)

- **45DTE bucket validation** — `intraday_backtest.py` only simulates 0DTE/1-3DTE structures. Router's 45DTE path is out of scope; daily-track validation lives elsewhere (`spy_daily_backtest.py`).
- **Phase 4b structure builder** — this backtest uses `intraday_backtest.py`'s structures (3-pt OTM short, 5-pt wing, ATM debit long, etc.). Phase 4b's per-sub-strategy structures are a separate spec.
- **Parameter sweep across router knobs** — single config, validated as-is. Multi-config sweeps invite overfitting and contradict the walk-forward / honest-ML discipline.
- **Multi-tick intraday replay** — router fires once per day at 9:45 ET (post opening-range). Replaying every 5-min tick adds compute without changing the verdict (dedup blocks re-entry into the same combo).
- **Threshold calibration** — separate exercise after raw stats are in hand.
- **Live wiring** — this is a backtest module. Promotion to a live behavior change is a separate decision after the verdict.

## Locked Decisions

| Decision | Choice | Source |
|---|---|---|
| Backtest scope | Wrap existing `intraday_backtest.py` with the entry router; reuse all structures, exits, and pricing | User chose "Wrap existing" over new-from-scratch or gate-only |
| Parameter strategy | Single config = current Phase 3 settings (`ENTRY_TIER_MINIMUM="high"`, `ULTRA_CONVICTION_DOUBLE_DTE_SCORE=85`, `INTRADAY_PER_COMBO_DAILY_CAP=2`, `INTRADAY_DTE_MORNING_CUTOFF="12:30"`) | User chose "Single config" |
| Verdict frame | Both Δ vs no-router AND absolute OOS thresholds; thresholds TBD via calibration exercise | User chose "Router vs no-router AND absolute thresholds" |
| Time window | Rolling walk-forward, 6mo train / 3mo test, sliding monthly | User chose "Rolling walk-forward" |
| Train window | Kept (contextual; no learning role in this spec) | User: "yes train window is worth keeping" |
| Verdict thresholds | Deferred — backtest emits raw stats; thresholds picked by separate exercise | User: "exercise to see what variation works" |
| Baseline definition | Same router, with tier-gate disabled (DTE assignment + dedup kept identical) | Apples-to-apples isolation of the tier filter |
| Tick-of-day | One evaluation per day at 9:45 ET (post-OR) | Matches `intraday_backtest.py`'s single-entry-per-day model |
| Module location | `backtests/intraday_router_wf.py` (orchestrator) + `backtests/router_setup_builder.py` (historical SPYSetup factory) | Project convention: per-purpose modules under `backtests/` |

## Architecture

```
backtests/intraday_router_wf.py    ← orchestrator
    │
    ├── generate_windows(start, end, train=6mo, test=3mo, step=1mo)
    │
    └── for each (train_range, test_range) in windows:
          for date in test_range:
              setup_list = build_historical_setup(date)         # router_setup_builder
              for setup in setup_list:
                  buckets_T  = route(setup, 09:45_ET, MockBroker())
                  buckets_B  = route_no_tier_gate(setup, 09:45_ET, MockBroker())
                  for (strat, bucket) in buckets_T:
                      trades_T.append(simulate_day(date, setup, strat, bucket))
                  for (strat, bucket) in buckets_B:
                      trades_B.append(simulate_day(date, setup, strat, bucket))
          window_stats(trades_T, trades_B) → WindowResult
    aggregate(windows) → verdict report
```

**Apples-to-apples invariant:** a day skipped in treatment (for any non-router reason — no data, empty setup, sim error) is also skipped in baseline, and vice versa. The runner enforces this with a per-day exception scope.

## Components

```
backtests/intraday_router_wf.py        (~250–350 lines, new)
  - generate_windows()
  - run_window(train_range, test_range, *, get_setup, get_pnl) → WindowResult
  - route_no_tier_gate(setup, ts, broker)       # ENTRY_TIER_MINIMUM context manager
  - _MockBroker (inlined, ~25 lines)
  - window_stats(trades_T, trades_B) → dict
  - aggregate_verdict(windows) → summary
  - if __name__ == "__main__": CLI entry, dumps stats JSON + markdown table

backtests/router_setup_builder.py       (~150–200 lines, new)
  - load_daily_history(through_date) → DataFrame
  - load_intraday_window(date, start=09:30, end=09:45) → DataFrame
  - build_historical_setup(date) → list[SPYSetup]
       # internally: df_daily + df_intraday → SPYOptionsEngine().analyze()

tests/test_intraday_router_wf.py         (new)
tests/test_router_setup_builder.py        (new)
```

**Reused without modification:**
- `data/intraday_data.py` — fully-paginated, parquet-cached intraday SPY bars
- `data/options_history.py` — real option intraday aggregates (incl. 0DTE)
- `signals/spy_options_engine.py` — produces `SPYSetup` from DataFrames (no live deps; verified)
- `signals/intraday_entry_router.py` — system under test
- `backtests/intraday_backtest.py` — day simulator (treatment + baseline both use it)
- `backtests/spy_history.csv` — daily SPY OHLCV, back to 2021-05-24
- `backtests/wf_common.py` — date utilities if applicable

## Data Flow

Per simulated day:

```
date = 2024-07-15
1. df_daily    ← spy_history.csv through 2024-07-12 (last completed daily bar)
2. df_intraday ← intraday_data.py for 2024-07-15, slice 09:30 – 09:45 ET
3. setups      ← SPYOptionsEngine().analyze(df_daily, df_intraday)
                 → 0..3 SPYSetup objects

4. TREATMENT:
     for setup in setups:
       buckets = route(setup, ts=2024-07-15 09:45 ET, MockBroker())
       for (strategy, dte_bucket) in buckets:
         outcome = intraday_backtest.simulate_day(date, setup, strategy, dte_bucket)
         trades_T.append(outcome)

5. BASELINE:
     for setup in setups:
       with _bypass_tier_gate():
         buckets = route(setup, ts=2024-07-15 09:45 ET, MockBroker())
       for (strategy, dte_bucket) in buckets:
         outcome = intraday_backtest.simulate_day(...)
         trades_B.append(outcome)
```

**`_bypass_tier_gate()`** is a context manager that temporarily sets `config.ENTRY_TIER_MINIMUM = "watch"` (the lowest tier rank) and restores it on exit, including on exception. Tested for restoration.

**Why one tick per day at 9:45 ET:**
- Matches `intraday_backtest.py`'s single-entry-per-day model
- `route()`'s dedup blocks re-entry into the same `(strategy, dte_bucket)` anyway
- Scope of this work is "validate the gate," not "validate the intraday timing"

**All datetimes constructed in `pytz.timezone("US/Eastern")`** — matches live scanner convention. Asserted at function boundaries.

## Walk-Forward Schedule

```
generate_windows(start=2024-01-02, end=2025-12-31,
                 train_months=6, test_months=3, step_months=1)
```

Yields ~16 overlapping windows. Each window:
- `train_range`: 6mo immediately preceding test (contextual, no learning role this spec)
- `test_range`: 3mo OOS evaluation period
- Windows step 1mo at a time, so test ranges overlap — that's deliberate (more verdict samples per stretch of data, with the understanding that adjacent windows aren't independent)

Train window is kept per user direction; future specs can give it a learning role (parameter selection, regime calibration). For now it's a placeholder for that future structure.

## Verdict (thresholds deferred)

**Per-window stats emitted (raw):**
- `n_trades_T`, `n_trades_B`
- `n_days_evaluated`, `n_days_skipped`, `skip_reasons` (Counter)
- `pnl_T`, `pnl_B`, `delta_pnl_per_trade`
- `sharpe_T`, `sharpe_B`, `delta_sharpe`
- `win_rate_T`, `win_rate_B`
- `regime_breakdown` (per-regime treatment trade counts + PnL, if available from `simulate_day` output)

**Verdict-stub:** a function with TBD threshold constants at the top:
```python
MIN_DELTA_PNL_PER_TRADE = None   # filled by calibration exercise
MIN_OOS_SHARPE          = None
MIN_OOS_WIN_RATE        = None
MIN_WINDOW_PASS_RATE    = None   # e.g. 0.60

def window_verdict(stats) -> Literal["pass", "fail", "inconclusive"]:
    if stats["n_trades_T"] < MIN_N_FOR_VERDICT: return "inconclusive"
    if all thresholds met: return "pass"
    return "fail"
```

Stubs return `"raw"` until thresholds are filled. The first run produces only the stats table.

**Threshold-picking exercise (separate, follow-up):**
- Run the WF once to get the raw stats matrix.
- Compare against reference benchmarks (the 5/21 0DTE no-router baseline at `-$515 / Sharpe -2.23` on full-year 2024).
- Use bootstrap CIs on Δ$/trade per window to inform the bar.
- Propose thresholds; user reviews; thresholds committed.

## Error Handling

| Failure | Where | Behavior |
|---|---|---|
| Polygon rate limit / timeout | `data/intraday_data.py` (already retries) | First-run hits the wall; cached re-runs are free. WF runner retries once at its layer, then logs day as `skipped:data_fetch`. |
| Option aggregates empty for an expiration | `data/options_history.py` | `simulate_day` already returns "no-trade." Counted as `skipped:no_option_data`. |
| `analyze()` returns `[]` (no qualifying setup) | Setup builder | Not an error — legitimate "no signal." Day yields zero trades in both T and B. |
| Insufficient daily history (< 30 bars) | Setup builder | Logged and skipped; only at very start of data range. |
| Window has too few trades for stats | Verdict aggregator | `n_trades < MIN_N_FOR_VERDICT` (default 10) → window reported as `inconclusive`; excluded from pass-rate denominator. |
| Mock broker state leaks across days | `_MockBroker` instantiated fresh per day | Per-day instance; impossible to leak by construction. Tested. |
| Time-zone confusion (Eastern vs Central host) | All datetimes | `pytz.timezone("US/Eastern")` everywhere; assertions at function boundaries. |
| `route()` raises unexpectedly | `run_window` per-day scope | Caught, day logged as `skipped:router_error`, dropped from BOTH T and B. |

**Apples-to-apples invariant (hard rule):** any day skipped for any reason — data, setup, sim, router error — drops from BOTH treatment and baseline.

**Logging:** loguru. One `INFO` line per window with headline stats; `DEBUG` per skipped day with the reason. Matches existing backtest style.

## Testing

```
tests/test_router_setup_builder.py
  ✓ builds SPYSetup for a known 2024 date matching expected score/conviction
  ✓ returns [] when daily history is too short
  ✓ produces tz-aware Eastern datetimes throughout
  ✓ handles missing intraday data gracefully (skip, no crash)

tests/test_intraday_router_wf.py
  ✓ window generator: known span → expected count + monotonic + train-before-test
  ✓ apples-to-apples invariant: synthetic skipped day drops from both T and B
  ✓ MockBroker: get_trades_by + _entry_count_today_by_combo correct
  ✓ MockBroker: fresh-per-day prevents cross-day state leak
  ✓ _bypass_tier_gate context manager: restores ENTRY_TIER_MINIMUM on exit AND on exception
  ✓ window_stats: dummy trades in → expected aggregates out
  ✓ aggregate_verdict: dummy windows in → expected pass-rate out
  ✓ verdict-stub returns "raw" when thresholds are None
```

**End-to-end integration test:** `@pytest.mark.integration` — one short window (e.g. 2024-04 only), confirms the full pipeline completes without crash and produces non-empty stats. Excluded from the default `pytest -m "not integration"` per the project's existing convention.

## Out of Scope / Future Work

- **Threshold calibration exercise** — separate spec once raw stats are in hand. Bootstrap CIs on Δ$/trade, comparison to 5/21 baseline, threshold proposals.
- **45DTE bucket validation** — needs a different backtest harness (daily structures, not intraday options pricing).
- **Phase 4b structure builder validation** — when real per-sub-strategy structures replace the synthetic Phase 3 stubs in live, a parallel backtest spec validates them. May replace this spec's reuse of `intraday_backtest.py`'s structures.
- **Parameter sweep** — if the single-config verdict is "fail" but close to the threshold, a constrained sweep across `ULTRA_CONVICTION_DOUBLE_DTE_SCORE` and `INTRADAY_PER_COMBO_DAILY_CAP` may be warranted. Separate spec, walk-forward discipline preserved.
- **Multi-tick replay** — if the verdict hinges on entry timing, a follow-up spec replays every 5-min tick instead of just 9:45 ET.
- **Live promotion** — even on "pass" verdict, promotion to live behavior change (e.g., increasing `MAX_CONCURRENT_DISCIPLINED`) is a separate decision.
