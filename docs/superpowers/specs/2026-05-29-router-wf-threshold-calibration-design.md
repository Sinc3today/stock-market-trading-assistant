# Router WF Threshold Calibration — Design Spec

**Date:** 2026-05-29
**Status:** Approved design, pending implementation plan
**Author:** brainstormed with Claude Code

---

## Problem / Motivation

`backtests/intraday_router_wf.py` (shipped 2026-05-28) emits raw per-window stats and a verdict of `"raw"` because all four `MIN_*` thresholds are `None`. Until the thresholds are populated, the walk-forward can't actually answer the edge question — it just records data. This spec covers the follow-up calibration exercise the WF spec explicitly deferred.

The 2026-05-28 smoke run on Oct-Dec 2024 showed `n_T=46, n_B=110, ΔPnL/trade=-0.44`. That's one window — not enough to set thresholds. The full 2024-2025 run yields 16 windows of paired treatment/baseline data, which is enough to bootstrap.

Locked direction from brainstorming: **paired bootstrap on Δ$/trade per window + baseline-side null reference + sensitivity matrix → user picks the four thresholds informed by statistical justification.**

## Goals

- Provide a reproducible script that converts raw WF output → proposed threshold values with statistical backing.
- Surface the honest verdict if the bootstrap shows no detectable edge over the baseline (same shelving discipline as the 2026-05-21 0DTE work).
- Keep the editorial decision with the user: the script proposes, the user sets the constants.
- Address the known NaN-propagation bug in `window_verdict` before any calibration commits, so threshold checks don't silently misfire on zero-trade-side windows.

## Non-Goals (deferred)

- **CI-aware verdict logic** — `window_verdict` continues to use point estimates. The bootstrap CI informs threshold-setting, not per-run verdict computation.
- **Aggregate pass-rate calibration** — `MIN_WINDOW_PASS_RATE` stays at the spec's 60% heuristic (not bootstrapped from the data).
- **Cross-validated threshold inference** — no holdout split. The calibration uses all 16 windows because dropping half would weaken the already small sample.
- **Re-running calibration on different time windows** — single calibration against the 2024-2025 WF. Future re-calibration is a follow-up if and when more data accumulates.
- **Threshold setting itself** — the script proposes; the user commits the values. No auto-write to the module.
- **Other verdict knobs** (e.g., per-bucket thresholds, regime-conditional thresholds) — out of scope. The four existing `MIN_*` constants are the entire surface area.

## Locked Decisions

| Decision | Choice | Source |
|---|---|---|
| Calibration method | Bootstrap CIs + null baseline | User chose "Bootstrap CIs + null baseline" |
| Deliverable shape | Calibration module + user sets thresholds | User chose "Calibration module + you set thresholds" |
| NaN-bug fix timing | Standalone commit BEFORE calibration | User chose "Standalone fix commit first" |
| Aggregate threshold | Keep `MIN_WINDOW_PASS_RATE = 0.60` (spec heuristic, not bootstrapped) | Out of scope |
| Bootstrap shape | Paired by DAY for Δ$/trade (preserves same-day market conditions); marginal-on-trades for T-side absolute metrics | Brainstorming Section 2 |
| Resample count | 1000 iterations per window | Standard bootstrap default; configurable via CLI |
| Bootstrap seed | Fixed (`seed=42`) for reproducibility | Standard |
| Candidate threshold grids | Δ$/trade: `{0.0, 0.5, 1.0, 2.0}`; Sharpe: `{0.1, 0.3, 0.5}`; WR: `{0.50, 0.55, 0.60}`; PnL: `{0, 100, 500}` | Brainstorming Section 2 |
| Output | Markdown report + terse console recommendation | Brainstorming Section 3 |
| Module location | `backtests/calibrate_router_wf.py` | Project convention |

## Architecture

```
Phase A: NaN-guard fix (commit 1)
    └── backtests/intraday_router_wf.py:window_verdict
          add math.isnan(stats["delta_pnl_per_trade"]) → "inconclusive"
          (insufficient-data signal, matching the n_trades_T < min_n branch)

Phase B: Full WF run (no code, one command)
    └── python -m backtests.intraday_router_wf \
              --start 2024-01-02 --end 2025-12-31 \
              --out logs/router_wf_full.json

Phase C: Calibration module (commit 2)
    backtests/calibrate_router_wf.py
        ├── load_wf_report(path) → dict
        ├── paired_bootstrap_delta(window, n_iter=1000, seed=42)
        │       → (point_estimate, ci_low, ci_high)
        ├── marginal_bootstrap_metric(trades_T, metric_fn, n_iter, seed)
        │       → (point, ci_low, ci_high)
        ├── propose_min_delta(windows, candidates) → [(candidate, point_pass_rate, ci_pass_rate), …]
        ├── propose_floor(windows, metric_key, candidates) → [(candidate, pass_rate), …]
        ├── sensitivity_matrix(windows, delta_candidates, sharpe_candidates) → 2D pass-rate
        ├── render_markdown(report, proposals) → str
        └── if __name__ == "__main__": CLI entry

    tests/test_calibrate_router_wf.py

Phase D: Run calibration
    └── python -m backtests.calibrate_router_wf \
              --report logs/router_wf_full.json \
              --out logs/router_wf_calibration.md \
              --bootstrap 1000 --seed 42

Phase E: User sets MIN_* constants (commit 3)
    └── backtests/intraday_router_wf.py
          MIN_DELTA_PNL_PER_TRADE = <user choice>
          MIN_OOS_PNL             = <user choice>
          MIN_OOS_SHARPE          = <user choice>
          MIN_OOS_WIN_RATE        = <user choice>

Phase F: Re-run WF with thresholds active
    └── Same CLI as Phase B; aggregate verdict now pass/fail.

Phase G: BUILD_LOG entry documenting the calibration outcome.
```

## Components

```
backtests/calibrate_router_wf.py    (~250–350 lines, new)
  - load_wf_report(path) → dict
  - paired_bootstrap_delta(window, n_iter, seed)
       # Resample DAYS with replacement. For each sampled day, take ALL its
       # T and B trades. Compute per-iter Δ = mean(T_pnl) − mean(B_pnl).
       # Returns (point_estimate, ci_low_pct, ci_high_pct).
  - marginal_bootstrap_metric(trades, metric_fn, n_iter, seed)
       # Resample trades with replacement. Apply metric_fn (e.g., mean, sharpe,
       # win_rate). Returns (point, ci_low, ci_high).
  - propose_min_delta(windows, candidates)
       # For each candidate value: count windows where point Δ > candidate
       # (point_pass_rate) AND where CI lower bound > candidate (ci_pass_rate).
       # The candidate where these converge is the statistical separator.
  - propose_floor(windows, metric_key, candidates)
       # For each candidate value: count windows where T-side metric > candidate.
  - sensitivity_matrix(windows, delta_candidates, sharpe_candidates)
       # For each (Δ, Sharpe) pair: count windows passing ALL four MIN_* with
       # the current PnL+WR pre-fixed; return % aggregate pass.
  - null_reference_summary(windows)
       # Aggregate baseline-side stats: median per-window PnL, median Sharpe,
       # median win rate. Plus the fixed 5/21 backtest reference line.
  - render_markdown(report, proposals, null_ref, sensitivity)
       # Build the markdown structure from Section 3 of the design.
  - if __name__ == "__main__":
       # argparse: --report (default logs/router_wf_full.json),
       # --out (default logs/router_wf_calibration.md),
       # --bootstrap (default 1000), --seed (default 42)

tests/test_calibrate_router_wf.py  (new)
```

**Reused without modification:**
- `backtests/intraday_router_wf.py` (except for the NaN-guard fix in Phase A)
- The JSON report shape emitted by `run_walk_forward`'s CLI

**Required input shape** (from `run_walk_forward`'s output):
- `report["windows"]` is a list of window dicts
- Each window has `stats.n_trades_T`, `stats.n_trades_B`, `stats.pnl_T`, `stats.pnl_B`, `stats.sharpe_T`, `stats.win_rate_T`, `stats.delta_pnl_per_trade`, plus `train_range`, `test_range`
- **However** — the current `run_window` output does NOT include per-trade lists, only aggregated stats. The bootstrap NEEDS per-trade pnl values, not just aggregates. This is a hard constraint surfaced during the spec write.

### Surface fix required to enable Phase C

Phase A (the NaN-guard fix) is small. **Phase A also needs to include** a small extension to `run_window` so it persists the per-trade data needed by the bootstrap:
- Add `trades_T` and `trades_B` (the raw trade-dict lists) to the window result. Each trade dict already contains `pnl_dollars`, `strategy`, `dte_bucket`, and a `date` field. The bootstrap reads from these.
- JSON-serialization-friendly (already plain dicts).
- Memory footprint: ~50-200 trades per window × 16 windows × ~100 bytes per trade dict = trivial.

This addition is a one-line code change in `run_window` (extend the returned dict) plus a trivial test. Bundling it with the NaN fix keeps Phase A as a single small commit that "makes the WF output calibration-ready."

## Bootstrap Methodology (per window)

**Paired bootstrap on Δ$/trade:**
- Group trades by date for both T and B.
- Days set: union of dates present in either side.
- For `n_iter` iterations:
  1. Sample `len(days)` days with replacement.
  2. Flatten T trades on sampled days into `sampled_T`. Same for B.
  3. Compute `delta = mean(sampled_T pnl) - mean(sampled_B pnl)`.
- 95% CI = (2.5th percentile, 97.5th percentile) of the resulting `delta` values.
- Point estimate = `mean(window.trades_T pnl) - mean(window.trades_B pnl)` from original data.

**Marginal bootstrap on T-side absolute metrics:**
- For each metric (PnL sum, Sharpe = mean/std, win_rate = wins/n):
  - Resample T trades with replacement, compute metric per iteration.
  - 95% CI from the distribution.

Edge cases:
- Window with `n_trades_T == 0` OR `n_trades_B == 0` → bootstrap returns `NaN` for Δ. Reported as "insufficient data" in the markdown table.
- Window with all-identical pnls (degenerate) → CI collapses to point estimate. Surfaced explicitly in the report.

## Threshold Proposal Logic

**For `MIN_DELTA_PNL_PER_TRADE`:**
- Candidates: `[0.0, 0.5, 1.0, 2.0]`.
- For each candidate:
  - `point_pass_rate` = % of windows where point Δ > candidate
  - `ci_pass_rate` = % of windows where CI lower bound > candidate
- Recommendation: the candidate where `point_pass_rate ≈ ci_pass_rate` AND `ci_pass_rate ≥ 50%`. This is the statistically defensible "separator."
- If no candidate satisfies this, recommend `0.0` and document "no detectable edge" in the report.

**For `MIN_OOS_SHARPE`, `MIN_OOS_WIN_RATE`, `MIN_OOS_PNL`:**
- Pragmatic candidates per Section 2.
- For each: pass_rate = % windows where T-side metric > candidate.
- Recommendation: smallest candidate where pass_rate ≥ 50% (so the median window passes). If no candidate does, recommend the floor candidate (Sharpe 0.1, WR 0.50, PnL 0) and note the implication.

## Sensitivity Matrix

3×3 grid: `MIN_DELTA` × `MIN_OOS_SHARPE`, with `MIN_OOS_PNL` and `MIN_OOS_WIN_RATE` fixed at the recommended values.

Each cell shows the resulting aggregate pass-rate (% of windows that would receive verdict="pass" under the full four-threshold check).

Highlights the user's flexibility: see how moving Δ from 0.5 to 1.0 changes the headline.

## Verdict Outcome Honesty

The calibration script must print the honest result, even when unflattering:

- If at recommended thresholds the aggregate pass-rate is ≤ 30%, the report's recommendation block reads: *"Phase 3 entry router does not show detectable edge over the baseline at any reasonable threshold setting. Recommend shelving Phase 3 and revisiting the entry-level retune, same discipline as 2026-05-21 0DTE work."*
- If between 30-60%: *"Inconclusive — edge signal exists but is below the 60% pass-rate bar. Document and run again after collecting more OOS data."*
- If ≥ 60%: *"Edge confirmed at recommended thresholds. Set the four MIN_* constants and proceed."*

## Error Handling

| Failure | Behavior |
|---|---|
| WF report file missing or malformed | Raise `FileNotFoundError` / `ValueError` with the report path. CLI exits non-zero. |
| Window has 0 trades on both sides | Skip in bootstrap; record as "no data" in the report. |
| Bootstrap produces all-NaN due to degenerate data | Window's row in the report shows "—" for CI; doesn't crash. |
| Sensitivity matrix has zero windows pass at any setting | Report rendered with all "0%" cells and the shelving-recommended block. |
| Out file path's directory missing | `os.makedirs(dirname, exist_ok=True)` (matches the WF CLI's pattern). |

## Testing

`tests/test_calibrate_router_wf.py` — pure-function unit tests, no Polygon dependency:

- `test_paired_bootstrap_preserves_day_pairing` — synthetic 2-day window with deliberately mismatched per-day signs. Paired bootstrap must yield same-sign-as-expected; would catch a bug where days got crossed.
- `test_paired_bootstrap_ci_widens_with_fewer_days` — same per-day variance, varying day count. CI width inversely related to sqrt(n_days).
- `test_marginal_bootstrap_sharpe_brackets_known_value` — synthetic T trades with known mean/std. Bootstrap Sharpe CI contains the true value with high probability.
- `test_propose_min_delta_recommends_zero_when_no_edge` — synthetic data where every window's point Δ ≤ 0 → recommendation is 0.0 with "no detectable edge" flag.
- `test_propose_min_delta_recommends_separator_when_clear_edge` — synthetic data where 80% of windows have CI low > 0.5 → recommendation is 0.5.
- `test_sensitivity_matrix_monotone_in_each_axis` — pass-rate decreases monotonically as MIN_DELTA rises (column-wise) and as MIN_OOS_SHARPE rises (row-wise).
- `test_render_markdown_includes_all_sections` — smoke test that the markdown output contains the headline sections (Per-window, Null reference, Proposals, Sensitivity, Recommendation).
- `test_seeded_bootstrap_is_deterministic` — same input + same seed → identical CI.

Integration verification (manual, not automated): run Phase D against the real WF output and eyeball the markdown.

## Out of Scope / Future Work

- **CI-aware verdict** — replace point-estimate threshold check in `window_verdict` with CI-aware check. Currently the verdict uses point estimates and the CI is informational only.
- **Cross-validated calibration** — split windows train/test, infer thresholds from train, validate on test. Requires more windows than we have.
- **Per-bucket thresholds** — 0DTE and 1-3DTE might warrant separate thresholds. Right now `by_bucket` data is emitted but verdict uses aggregate only.
- **Regime-conditional thresholds** — TBD; needs `regime_breakdown` in `window_stats` first (deferred from the WF spec).
- **Re-calibration on different time windows** — once 2026 data accumulates, re-running calibration is a separate exercise.
- **Auto-applying threshold values** — script proposes, user commits. No automated write to the module.

## If Calibration Says "Shelve" — Investigate Multi-Tick First

The WF backtest evaluates `route()` ONCE per day at 9:45 ET (post opening-range). Live, the router fires at every 5-min tick from 9:30-16:00 (~78 ticks/day). **The current backtest is structurally blind to any router edge that comes from intraday TIMING** rather than tier-gate filtering at the open:

- A 9:45 setup scoring 60 (standard tier, blocked) might escalate to 75 (high) at 11:00 after morning chop. Live catches it; backtest misses it entirely.
- Setups that emerge fresh from afternoon volatility (e.g., a 2:30 PM iron_condor after a sideways morning) never appear.
- Re-entries after a target-hit early exit aren't simulated (current sim never closes mid-day, but live would re-arm after closure).

**Decision branch point:** if calibration's recommendation block reads "no detectable edge" or "inconclusive":

1. **Do NOT shelve Phase 3 on calibration evidence alone.** The honest verdict is "no edge under the single-tick assumption," not "no edge."
2. **Spec the multi-tick replay** as the next investigation (separate design cycle, cost comparable to the WF backtest itself ~1000 LOC). Build a multi-tick state machine that maintains intraday position state across 5-min ticks, calls `route()` at each tick, handles dedup and per-combo daily caps the way live does, and re-arms after early exits.
3. **Re-run calibration against the multi-tick output.** If THAT still shows no edge, then shelving with the same discipline as the 2026-05-21 0DTE work is warranted.
4. **Only if multi-tick also shows no edge** does "shelve and revisit the entry-level retune" apply.

Skipping step 2 risks repeating the 0DTE shelving pattern on incomplete evidence — the entry-level retune memo specifically warned that "different intraday confirmation" was one of the revisit candidates, and timing-of-entry IS a form of intraday confirmation we haven't tested.
