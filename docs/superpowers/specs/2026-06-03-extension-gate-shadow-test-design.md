# Extension-Gate Shadow-Test — Design

**Date:** 2026-06-03
**Status:** Design approved; ready for implementation plan.
**Branch (anticipated):** `extension-gate-shadow-test`

## Problem & Goal

The daily regime's **extension gate** (`signals/regime_detector.py:283`, cap
`EXTENDED_TREND_MAX_PCT = 9.0`) returns `SKIP — trend too extended` whenever SPY
is >9% above its 200-MA. It has 5-year backtest support (lifted win-rate 50%→60%,
+$2,420, Sharpe 1.73→2.49), but it has now fired ~14 straight sessions during a
grinding uptrend that is NOT mean-reverting — possibly leaving money on the table
in the *current* regime. We never record the trades it skips, so we can't tell.

**Goal (anti-bias / falsification on the daily path):** on each extension-skip
day, record and score the counterfactual "trade we didn't take," accumulate its
expectancy, and when the shadow consistently beats the gate, route a proposal to
**relax the cap** through the existing hypothesis engine. This is the daily-path
analogue of the intraday learning-book falsification sandbox. See
[[feedback_falsification_anti_bias]].

**Scope (v1):** the extension gate only (`TRENDING_UP_CALM & not tradeable &
"extended"`). Generalizing to other daily SKIP gates is a follow-up.

## Key Decisions

1. **Score BOTH** (user): directional accuracy (would the bullish call have been
   right) AND a priced paper **shadow book** (real dollar P&L the gate cost).
2. **Reuse the existing trade lifecycle:** shadow trades are tagged `book="shadow"`,
   `source="auto-paper"`, so `exit_manager`/`expiry_resolver` manage+close them
   automatically; excluded from disciplined/learning stats (a third book).
3. **Surface via the hypothesis engine:** `EXTENDED_TREND_MAX_PCT` becomes a
   whitelisted `TUNABLE_PARAM`; when shadow expectancy clears a floor over N
   extension-skip days, the engine proposes raising the cap → `hypothesis_runner`
   backtests → human promotes (no auto-edit of source, per loop rule 13).
4. **Isolated module invoked from the daily path** — not embedded in the locked
   `SPYDailyStrategy`/`regime_detector`.

## Architecture

### New module `learning/shadow_tester.py`

```
run_shadow(regime_result, spy_df, ivr_current, options_chain, today=None) -> dict | None
```
- Returns `None` unless `regime_result` is the extension-skip
  (`regime == TRENDING_UP_CALM` AND `not tradeable` AND the reason contains
  "extended"/"extension"). Gated by `config.SHADOW_TEST_ENABLED` (default True).
- Builds the would-be bull structure via `OptionsLayer.analyze(...)` — the SAME
  call the daily play uses — choosing bull put credit (IVR≥50 & not
  `PREFER_DEBIT_OVER_CREDIT`) vs bull call debit, matching `regime_detector`'s
  own play selection.
- Records a 1-contract paper trade via `TradeRecorder.log_entry(...,
  book="shadow", source=AUTO_SOURCE, notes=<[SHADOW] ...>)`.
- Records `entry_spy` on the shadow trade so the directional counterfactual can
  be scored. **Directional scoring (no new scheduled job):** at EOD, the existing
  `16:05 outcome_resolver` — which already fetches the SPY close — also stamps the
  extension-skip day's counterfactual directional result onto the shadow record
  via `outcome_resolver._score("bullish", entry_spy, close)` (this does NOT change
  the real prediction's `skip` status; it adds a `shadow_directional` field).
  `shadow_stats` reads these. The two metrics are distinct: directional =
  same-day "did SPY close up" (the bull bet's direction); shadow-book P&L = the
  multi-day structure's realized P&L from its lifecycle.
- Wrapped so any failure (no chain, pricing) yields no shadow trade that day
  (logged), never disturbing the real daily play.

```
shadow_stats(n_days=30) -> dict
```
- Rolling expectancy over the last N extension-skip days: `{n, closed_pnl,
  open_mtm, directional_win_rate, win, loss}` from `book="shadow"` trades.
- Read by the hypothesis engine to decide whether to propose relaxing the cap.

### Hypothesis-engine integration

- Add `("signals.regime_detector", "EXTENDED_TREND_MAX_PCT"): {"type": "float",
  "min": 9.0, "max": 15.0}` to `learning/hypothesis_engine.TUNABLE_PARAMS`
  (raise-only band; never below the backtested 9.0).
- The engine's daily proposal context gains `shadow_stats()`; when
  `closed_pnl > 0` AND `directional_win_rate >= config.SHADOW_MIN_WINRATE` AND
  `n >= config.SHADOW_MIN_DAYS`, the engine is told the gate is under
  disconfirming pressure and may propose raising `EXTENDED_TREND_MAX_PCT`. The
  existing `hypothesis_runner` backtests the change; promotion stays human.

### Config (config.py)
- `SHADOW_TEST_ENABLED = True` (kill switch).
- `SHADOW_MIN_DAYS = 10`, `SHADOW_MIN_WINRATE = 0.55` (the proposal floor).

## Data flow

```
09:15 daily job (spy_daily_scheduler):
  SPYDailyStrategy regime classify
    extension-skip?  → real daily play SKIPS (unchanged)
                     → shadow_tester.run_shadow(...) opens a book="shadow" paper
                       trade + logs the directional counterfactual
  exit_manager (every 5min) / expiry_resolver (16:10): manage+close shadow trades
    automatically (source=auto-paper, picked up by is_auto_paper)
  16:05 outcome_resolver: scores the day's directional prediction (already runs)
  Sat hypothesis_engine: reads shadow_stats(); proposes raising EXTENDED_TREND_MAX_PCT
    when the floor is cleared → hypothesis_runner backtests → human promotes
```

## Error handling

- `run_shadow` is called inside the daily job's existing try/except (Standing
  Rule #10) — a shadow failure cannot disturb the real daily play.
- No chain / unpriceable structure → no shadow trade that day, logged (same
  honesty discipline as the intraday structure builder).
- `shadow_stats` tolerates zero shadow trades (returns `n=0`, neutral) so the
  hypothesis engine never proposes on an empty sample.

## Testing (TDD)

- `run_shadow` fires ONLY on the extension-skip regime — returns None on
  tradeable days, on other skip reasons (separation, elevated-vol), and when
  `SHADOW_TEST_ENABLED=False`.
- Builds bull put credit when IVR≥50 (not prefer-debit) vs bull call debit
  otherwise — matching `regime_detector`'s selection.
- Records the trade with `book="shadow"`, `source="auto-paper"`, and the
  directional call; size 1.
- Directional scoring matches `outcome_resolver._score` (bullish + close>entry →
  correct).
- `shadow_stats` aggregates only `book="shadow"` trades over N days; `n=0` when
  none; computes closed_pnl + win-rate.
- `EXTENDED_TREND_MAX_PCT` is in `TUNABLE_PARAMS` with the raise-only band; the
  hypothesis engine proposes the relax only when `closed_pnl>0 &
  win_rate>=floor & n>=min_days`, never on an empty/negative sample.
- A shadow failure (bad chain) does not raise out of the daily job.

## Follow-ups (noted, not in this spec)

- Generalize the shadow-test to other daily SKIP gates (separation, downtrend).
- A "shadow book" view in the dashboard alongside disciplined/learning.
- Surface shadow expectancy in the falsificationist reflector's daily KB.
