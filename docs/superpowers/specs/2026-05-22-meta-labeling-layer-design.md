# Meta-Labeling Layer — Design Spec

**Date:** 2026-05-22
**Status:** Approved design, pending implementation plan
**Author:** brainstormed with Claude Code

---

## Problem / Motivation

The bot's primary model — `regime_detector` (the 6-regime SPY classifier) plus
`spy_daily_strategy` (regime → option structure) — decides *what* to trade and
*which side*. It does not distinguish a strong setup from a marginal one inside
the same regime: every tradeable day in a regime gets the same treatment.

The user wants the "honest ML" version of conditioning strategy on the many
variables that drive price — without the overfitting trap that a brute-force
"search hundreds of variables" approach walks straight into. A walk-forward
experiment (see `backtests/condor_in_trend_wf.py`, commit `953f18d`) made the
trap tangible: out-of-sample results are dominated by which regime the test
window lands in, and adding knobs/variables amplifies the false-discovery risk
rather than reducing it.

**Meta-labeling** is the disciplined answer. Per Lopez de Prado: the primary
model sets the *side*; a secondary model sets the *size* — and here size is
just {0, 1} (skip / take). It adds exactly one well-bounded decision, validated
out-of-sample, instead of a fishing expedition.

## Goals

- A secondary classifier that scores each trade the primary would take with a
  calibrated `P(win)`, then **skips** trades below a threshold and **tags**
  survivors with a low/med/high conviction tier.
- Ships ONLY if it beats "take everything" out-of-sample (hard ship/no-ship gate).
- Provide the calibration record needed to *later* justify position sizing —
  without risking money on confidence before it is proven.
- Test whether Fair Value Gap (FVG) context improves trade selection, as a
  measured experiment, not a hard-coded belief.

## Non-Goals (deferred, deliberately)

- **Position sizing.** Tiers never change size in v1. Sizing is an earned
  upgrade, gated on tiers proving calibrated out-of-sample.
- **Intraday FVG.** v1 uses daily-bar FVG only (works over the full 5yr history
  with no new data dependency). Intraday FVG (~2yr of data) is a later experiment.
- **Tree ensembles / heavy models.** Logistic regression only.
- **Non-SPY tickers.** Matches the current `spy_focus` posture.
- **New primary strategies** (including FVG as a standalone entry engine).

## Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| What the layer does | **Filter + confidence tier** (no sizing) | Same risk as a pure gate, plus a near-free calibration dataset that tells us whether confidence is real before it ever touches size. |
| Feature set | **Core baseline first; daily-FVG as experiment #1** | ~160–600 trades/regime means every extra feature raises overfit risk. Baseline must be beaten OOS; FVG's marginal lift is measured in isolation. |
| Label source | **Bootstrap on 5yr backtest, recalibrate on live paper outcomes** | Works today (backtest labels), then adapts via the existing self-learning loop. |
| Model | **Logistic regression** (L2-regularized, scikit-learn) | Linear, calibrated, interpretable coefficients, hard to overfit a few hundred rows. Tiers = probability bins. |

## Architecture

The meta-labeler is a **new gate**, not a new strategy. The primary model is
untouched.

```
regime_detector ─► spy_daily_strategy picks play ─► feature_builder
                                                         │
                                                         ▼
                                              meta_labeler.score()
                                                  P(win) + tier
                                                         │
                                   P < threshold ──► SKIP
                                   P ≥ threshold ──► take @ 1 contract,
                                                     tag low/med/high
                                                         │
                                                         ▼
                                              gates ─► alert / journal
```

## Components

All new files are small and single-purpose.

### `signals/feature_builder.py` — train/inference parity (most important)
One function turns a day into a feature vector, called by **both** the training
path (backtest) and the live scoring path. This makes train/inference feature
drift — the classic meta-labeling bug — structurally impossible.

- Input: a `RegimeResult` (carries `metrics`: adx, vix, ivr, ma200_dist_%, etc.),
  the SPY history slice, the chosen play, and the regime.
- Output: an ordered, named feature vector (dict + stable column order).
- Baseline features: `adx`, `vix`, `ivr`, `ma200_dist_%`, plus the regime
  (one-hot or as a per-regime model split — see Open Questions) and the play side.
- FVG features (experiment #1, behind a flag): `inside_fvg`,
  `dist_to_nearest_fvg`, `fvg_size`.

### `indicators/fvg.py` — daily Fair Value Gap detection
Detects the 3-candle imbalance (candle-1 wick vs candle-3 wick non-overlap) on
daily bars and exposes the FVG features above. Pure function over an OHLC frame.
Has its own test file.

### `learning/meta_trainer.py` — offline training + validation
- Replays 5yr via `SPYBacktest` + `realistic_pricing` to build the labeled set:
  one row per tradeable day = `feature_builder` output + label (win=1/loss=0 from
  the realistic per-trade P&L).
- Trains the logistic regression.
- Runs **walk-forward** (expanding window: train on past, score the unseen next
  slice, aggregate OOS), comparing meta-filtered vs take-everything.
- Saves the model artifact + a metrics report (OOS expectancy, tier monotonicity,
  baseline-vs-FVG delta).
- Run manually first; later wired as a scheduled job.

### `signals/meta_labeler.py` — runtime scoring
Loads the trained artifact and exposes `score(features) -> {prob, tier, take}`.
No training at inference. If the artifact is missing → returns `take=True,
tier=None` (no-op, fail-open to current behaviour).

### `learning/meta_recalibrate.py` — periodic live recalibration
Weekly learning-loop job: pull resolved paper-trade outcomes, append to the
dataset, refit/recalibrate, re-run the OOS validation, and **swap the live
artifact only if it still passes the ship bar.** Never auto-edits source.

## Config + Safety

`config.py` additions:
- `META_LABEL_ENABLED` (default **False**)
- `META_PROB_THRESHOLD` (take cutoff, e.g. 0.55)
- `META_TIER_CUTOFFS` (e.g. med ≥ 0.55, high ≥ 0.70 — calibrated from training)
- `META_MODEL_PATH` (artifact location under `logs/learning/` or `models/`)

**Hard safety rule:** if `META_LABEL_ENABLED` is False OR the artifact is
missing/unloadable, the meta-gate is a no-op and the bot behaves exactly as it
does today. The meta-layer can never silently break or block live trading.

## The Validation Gate (ship / no-ship)

`META_LABEL_ENABLED` may be set True only if, on walk-forward OOS:

1. "primary + meta-filter" beats "primary alone" on expectancy by a meaningful
   margin, **and**
2. confidence tiers are **monotonic OOS** (high wins more than med, med more
   than low).

The **FVG experiment ships only if** the FVG-augmented feature set beats the
core-only baseline OOS. If a gate is not cleared, the change stays shelved
(same discipline that shelved 0DTE). This is a deliberate, human-promoted step
— consistent with self-learning rule 13 (the runner never edits source).

## Confidence Tiers

Calibrated probability bins from the training distribution (not arbitrary).
Surfaced in alerts and logged in the journal/predictions. They **never** change
position size in v1. Their early job is to accumulate the calibration record
that would justify sizing later: track whether high-tier trades actually win
more than low-tier, out-of-sample, over time.

## Integration with the Self-Learning Loop

- `meta_trainer` is a new offline step; `meta_recalibrate` becomes a scheduled
  learning job alongside the existing six.
- Predictions + paper outcomes are already logged → they feed recalibration.
- `hypothesis_engine` may later add `META_PROB_THRESHOLD` to its tunable
  whitelist (`TUNABLE_PARAMS`).
- Model artifacts live under `logs/learning/`; promotion (flipping
  `META_LABEL_ENABLED`) stays a deliberate human action.

## Testing Strategy

- **Feature parity:** the same day produces an identical vector through both the
  backtest path and the live path.
- **`fvg.py` units:** known 3-candle patterns produce expected gaps/features.
- **`meta_labeler`:** loads an artifact and scores; low prob → `take=False`;
  missing artifact → fail-open no-op.
- **Gate integration:** meta-gate skips below threshold, passes + tags above.
- **Walk-forward harness:** runs on synthetic data and computes OOS metrics.
- **No-op safety:** `META_LABEL_ENABLED=False` → behaviour identical to today.

## Data Notes / Constraints

- ~1,260 daily SPY rows over 5yr; tradeable days per regime are far fewer
  (e.g. ~166 `trending_up_calm`). Daily-overlapping trades are correlated, not
  independent — sample is small. This is *why* the model is linear and the
  feature set starts minimal.
- Backtest labels come from the BS-priced `realistic_pricing` engine (VIX as IV),
  so they are a model, not real fills. Trust rankings/signs over absolute dollars;
  live recalibration corrects toward reality over time.

## Open Questions (resolve during planning)

1. **Per-regime models vs one model with regime as a feature.** Per-regime is
   cleaner conceptually but splits the already-small sample further. Leaning:
   one pooled model with regime one-hot, revisit if a regime clearly behaves
   differently. To be decided in the implementation plan.
2. **Artifact format/location:** joblib/pickle under `logs/learning/` vs a new
   `models/` dir. Minor; decide in plan.
3. **Walk-forward fold geometry** for the meta-model (expanding vs rolling,
   fold size) — reuse patterns from `backtests/walk_forward.py`.

## File Inventory (new + touched)

New: `signals/feature_builder.py`, `indicators/fvg.py`,
`learning/meta_trainer.py`, `signals/meta_labeler.py`,
`learning/meta_recalibrate.py`, plus matching test files in `tests/`.

Touched: `config.py` (flags), `signals/gates.py` or `signals/spy_daily_strategy.py`
(the meta-gate hook), `learning/scheduler.py` (register recalibration job),
`requirements.txt` (scikit-learn).
