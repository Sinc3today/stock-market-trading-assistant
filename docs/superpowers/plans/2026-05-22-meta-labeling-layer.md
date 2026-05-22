# Meta-Labeling Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secondary "meta-label" model that scores each trade the regime strategy would take with a calibrated P(win), skips low-probability trades, and tags survivors with a low/med/high conviction tier — inert until proven out-of-sample.

**Architecture:** A new gate after the regime→play decision. A single `build_features` function (shared by the backtest training path and the live scoring path) guarantees train/inference parity. A logistic-regression model is trained offline on 5yr backtest outcomes, validated walk-forward, and loaded at runtime. The gate fails open: flag off or model missing → today's behaviour, unchanged.

**Tech Stack:** Python, pandas, numpy, scikit-learn (new), joblib (new). Reuses `signals/regime_detector.py`, `backtests/spy_daily_backtest.py`, `backtests/realistic_pricing.py`.

**Spec:** `docs/superpowers/specs/2026-05-22-meta-labeling-layer-design.md`

**Resolved open questions:** (1) one pooled model with regime one-hot, not per-regime; (2) scikit-learn as the dependency; (3) ship bar pinned in Task 6 (OOS expectancy improvement ≥ 10% AND tier monotonicity AND ≥ 60% of baseline trade count retained).

---

## File Structure

| File | Responsibility |
|---|---|
| `indicators/fvg.py` (new) | Daily Fair Value Gap detection + 3 features. Pure functions over an OHLC frame. |
| `signals/feature_builder.py` (new) | THE parity point: one `build_features(regime, metrics, spy_df, include_fvg)` → ordered feature dict. |
| `config.py` (modify) | `META_*` flags. |
| `learning/meta_dataset.py` (new) | Build the labeled training set (features + win/loss) from the 5yr backtest. |
| `learning/meta_trainer.py` (new) | Train logistic regression, walk-forward eval, save artifact + report. |
| `signals/meta_labeler.py` (new) | Runtime: load artifact, `score(features) → {prob, tier, take}`, fail-open. |
| `signals/spy_daily_strategy.py` (modify) | Hook the meta-gate into `build_today()` at the existing `tier` placeholder. |
| `learning/meta_recalibrate.py` (new) | Weekly job: append live outcomes, refit, re-validate, swap artifact only if it still passes. |
| `learning/scheduler.py` (modify) | Register the recalibration job. |
| `requirements.txt` (modify) | Add `scikit-learn`, `joblib`. |

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the libraries**

Append to `requirements.txt`:

```
scikit-learn==1.4.2
joblib==1.4.0
```

- [ ] **Step 2: Install into the project venv**

Run: `.venv/bin/pip install scikit-learn==1.4.2 joblib==1.4.0`
Expected: "Successfully installed joblib-1.4.0 scikit-learn-1.4.2 ..." (scipy/threadpoolctl may come along).

- [ ] **Step 3: Verify import**

Run: `.venv/bin/python -c "import sklearn, joblib; print(sklearn.__version__, joblib.__version__)"`
Expected: `1.4.2 1.4.0`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add scikit-learn + joblib for meta-labeling"
```

---

## Task 2: Daily Fair Value Gap features

**Files:**
- Create: `indicators/fvg.py`
- Test: `tests/test_fvg.py`

A bullish FVG over candles (i-2, i-1, i) exists when `low[i] > high[i-2]` — candle i-1's surge left an untraded gap zone `(high[i-2], low[i])`. Bearish FVG when `high[i] < low[i-2]`, zone `(high[i], low[i-2])`. A gap is "unfilled" if no later candle's range covers it. Features at a given `spot`: `inside_fvg` (1 if spot within any unfilled gap), `dist_to_nearest_fvg` (min distance to a gap edge, % of spot; 0 if none), `fvg_size` (height of the nearest unfilled gap, % of spot; 0 if none).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fvg.py
import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indicators.fvg import detect_fvgs, fvg_features


def _df(rows):
    # rows: list of (high, low). close/open not needed for gap geometry.
    return pd.DataFrame({"high": [r[0] for r in rows], "low": [r[1] for r in rows]})


def test_detects_bullish_gap():
    # candle 0 high=100; candle 1 surges; candle 2 low=102 > candle 0 high=100 → gap (100,102)
    df = _df([(100, 95), (108, 101), (110, 102)])
    gaps = detect_fvgs(df)
    assert any(g["type"] == "bull" and g["bottom"] == 100 and g["top"] == 102 for g in gaps)


def test_detects_bearish_gap():
    # candle 2 high=98 < candle 0 low=100 → gap (98,100)
    df = _df([(105, 100), (99, 92), (98, 90)])
    gaps = detect_fvgs(df)
    assert any(g["type"] == "bear" and g["bottom"] == 98 and g["top"] == 100 for g in gaps)


def test_filled_gap_excluded():
    # bullish gap (100,102) at i=2, then candle 3 trades back down through it → filled
    df = _df([(100, 95), (108, 101), (110, 102), (111, 99)])
    gaps = detect_fvgs(df)
    assert not any(g["bottom"] == 100 and g["top"] == 102 for g in gaps)


def test_features_inside_and_distance():
    df = _df([(100, 95), (108, 101), (110, 102)])  # unfilled bull gap (100,102)
    f_in = fvg_features(df, spot=101.0)   # inside the gap
    assert f_in["inside_fvg"] == 1
    assert f_in["fvg_size"] > 0
    f_out = fvg_features(df, spot=120.0)  # above everything
    assert f_out["inside_fvg"] == 0
    assert f_out["dist_to_nearest_fvg"] > 0


def test_features_empty_when_no_gaps():
    df = _df([(100, 95), (101, 96), (102, 97)])  # overlapping ranges, no gap
    f = fvg_features(df, spot=98.0)
    assert f == {"inside_fvg": 0, "dist_to_nearest_fvg": 0.0, "fvg_size": 0.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fvg.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'indicators.fvg'`

- [ ] **Step 3: Implement `indicators/fvg.py`**

```python
"""
indicators/fvg.py -- Daily Fair Value Gap (FVG) detection + features.

An FVG is a 3-candle imbalance: candle i-1's surge leaves an untraded gap
between candle i-2 and candle i that price tends to revisit ("fill"). We only
keep UNFILLED gaps (no later bar's range covers them) and expose three features
relative to a spot price for the meta-labeler to learn from.
"""

from __future__ import annotations

import pandas as pd


def detect_fvgs(df: pd.DataFrame) -> list[dict]:
    """Return unfilled FVGs as [{type, top, bottom, idx}], scanning the frame."""
    highs = df["high"].tolist()
    lows  = df["low"].tolist()
    gaps  = []
    for i in range(2, len(df)):
        # Bullish: candle i low above candle i-2 high → gap (i-2 high, i low)
        if lows[i] > highs[i - 2]:
            gaps.append({"type": "bull", "bottom": highs[i - 2], "top": lows[i], "idx": i})
        # Bearish: candle i high below candle i-2 low → gap (i high, i-2 low)
        elif highs[i] < lows[i - 2]:
            gaps.append({"type": "bear", "bottom": highs[i], "top": lows[i - 2], "idx": i})

    # Drop gaps later filled: any subsequent bar whose range overlaps the zone.
    unfilled = []
    for g in gaps:
        filled = False
        for j in range(g["idx"] + 1, len(df)):
            if lows[j] <= g["top"] and highs[j] >= g["bottom"]:
                filled = True
                break
        if not filled:
            unfilled.append(g)
    return unfilled


def fvg_features(df: pd.DataFrame, spot: float) -> dict:
    """inside_fvg / dist_to_nearest_fvg(%) / fvg_size(%) for the unfilled gaps."""
    empty = {"inside_fvg": 0, "dist_to_nearest_fvg": 0.0, "fvg_size": 0.0}
    if spot <= 0:
        return empty
    gaps = detect_fvgs(df)
    if not gaps:
        return empty

    inside = next((g for g in gaps if g["bottom"] <= spot <= g["top"]), None)
    if inside is not None:
        return {
            "inside_fvg": 1,
            "dist_to_nearest_fvg": 0.0,
            "fvg_size": round((inside["top"] - inside["bottom"]) / spot * 100, 3),
        }

    def edge_dist(g):
        return min(abs(spot - g["top"]), abs(spot - g["bottom"]))

    nearest = min(gaps, key=edge_dist)
    return {
        "inside_fvg": 0,
        "dist_to_nearest_fvg": round(edge_dist(nearest) / spot * 100, 3),
        "fvg_size": round((nearest["top"] - nearest["bottom"]) / spot * 100, 3),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fvg.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add indicators/fvg.py tests/test_fvg.py
git commit -m "feat: daily Fair Value Gap detection + features"
```

---

## Task 3: Feature builder (train/inference parity)

**Files:**
- Create: `signals/feature_builder.py`
- Test: `tests/test_feature_builder.py`

One function builds the feature vector from a `regime` string + a `metrics` dict (the exact dict shape `RegimeResult.metrics` and `SPYBacktest` rows both expose: keys `adx`, `vix`, `ivr`, `ma200_dist_%`, `spy_close`). Both the training path and the live path call it, so features cannot drift. `FEATURE_ORDER` fixes column order for the model.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_feature_builder.py
import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from signals.feature_builder import build_features, to_vector, FEATURE_ORDER

METRICS = {"adx": 34.0, "vix": 17.0, "ivr": 40.0, "ma200_dist_%": 9.4, "spy_close": 742.6}


def test_baseline_features_present_and_ordered():
    f = build_features("trending_up_calm", METRICS)
    assert f["adx"] == 34.0 and f["vix"] == 17.0 and f["ivr"] == 40.0
    assert f["ma200_dist_pct"] == 9.4
    assert f["regime_trending_up"] == 1
    assert f["regime_trending_down"] == 0
    assert f["regime_choppy_low_vol"] == 0
    # vector matches declared order, no FVG keys in baseline
    vec = to_vector(f)
    assert len(vec) == len(FEATURE_ORDER)
    assert "inside_fvg" not in f


def test_regime_onehot_choppy():
    f = build_features("choppy_low_vol", METRICS)
    assert f["regime_choppy_low_vol"] == 1
    assert f["regime_trending_up"] == 0


def test_fvg_features_appended_when_enabled():
    df = pd.DataFrame({"high": [100, 108, 110], "low": [95, 101, 102]})
    f = build_features("trending_up_calm", METRICS, spy_df=df, include_fvg=True)
    assert "inside_fvg" in f and "dist_to_nearest_fvg" in f and "fvg_size" in f


def test_parity_same_inputs_same_vector():
    # The whole point: identical inputs → identical vector regardless of caller.
    a = to_vector(build_features("trending_up_calm", METRICS))
    b = to_vector(build_features("trending_up_calm", dict(METRICS)))
    assert a == b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_feature_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'signals.feature_builder'`

- [ ] **Step 3: Implement `signals/feature_builder.py`**

```python
"""
signals/feature_builder.py -- Single source of truth for meta-label features.

Called by BOTH the offline training path (learning/meta_dataset.py, fed from
SPYBacktest rows) and the live scoring path (signals/spy_daily_strategy.py, fed
from RegimeResult). Identical inputs MUST yield an identical vector — that
parity is the reason this lives in one function.

`metrics` is the dict shape shared by RegimeResult.metrics and SPYBacktest rows:
keys adx, vix, ivr, ma200_dist_%, spy_close.
"""

from __future__ import annotations

import pandas as pd

from indicators.fvg import fvg_features

# Baseline feature order (model input order). FVG features are appended only
# when include_fvg=True and are NOT part of the baseline vector.
FEATURE_ORDER = [
    "adx", "vix", "ivr", "ma200_dist_pct",
    "regime_trending_up", "regime_trending_down", "regime_choppy_low_vol",
]
FVG_ORDER = ["inside_fvg", "dist_to_nearest_fvg", "fvg_size"]


def build_features(regime: str, metrics: dict,
                   spy_df: pd.DataFrame | None = None,
                   include_fvg: bool = False) -> dict:
    """Build the named feature dict for one day."""
    f = {
        "adx":            float(metrics.get("adx", 0.0)),
        "vix":            float(metrics.get("vix", 0.0)),
        "ivr":            float(metrics.get("ivr", 0.0)),
        "ma200_dist_pct": float(metrics.get("ma200_dist_%", 0.0)),
        "regime_trending_up":    1 if regime == "trending_up_calm" else 0,
        "regime_trending_down":  1 if regime == "trending_down_calm" else 0,
        "regime_choppy_low_vol": 1 if regime == "choppy_low_vol" else 0,
    }
    if include_fvg and spy_df is not None:
        f.update(fvg_features(spy_df, float(metrics.get("spy_close", 0.0))))
    return f


def to_vector(features: dict) -> list[float]:
    """Project a feature dict onto the fixed order (baseline + FVG if present)."""
    order = FEATURE_ORDER + (FVG_ORDER if "inside_fvg" in features else [])
    return [float(features[k]) for k in order]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_feature_builder.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add signals/feature_builder.py tests/test_feature_builder.py
git commit -m "feat: meta-label feature builder (train/inference parity)"
```

---

## Task 4: Config flags

**Files:**
- Modify: `config.py`
- Test: `tests/test_meta_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_meta_config.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_meta_flags_exist_with_safe_defaults():
    assert config.META_LABEL_ENABLED is False          # inert until proven
    assert 0.0 < config.META_PROB_THRESHOLD < 1.0
    assert config.META_TIER_CUTOFFS["med"] <= config.META_TIER_CUTOFFS["high"]
    assert config.META_MODEL_PATH.endswith(".joblib")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_meta_config.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'META_LABEL_ENABLED'`

- [ ] **Step 3: Add the flags to `config.py`**

Add after the `WATCHLIST LOADER` section (near the other path constants):

```python
# ─────────────────────────────────────────
# META-LABELING (secondary take/skip + conviction model)
# ─────────────────────────────────────────
# Inert until a trained model passes the walk-forward ship bar AND a human
# flips this to True. Flag off OR model missing => gate is a no-op.
META_LABEL_ENABLED  = False
META_PROB_THRESHOLD = 0.55                       # take if P(win) >= this
META_TIER_CUTOFFS   = {"med": 0.55, "high": 0.70}
META_MODEL_PATH     = "logs/learning/meta_model.joblib"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_meta_config.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_meta_config.py
git commit -m "feat: meta-label config flags (default off)"
```

---

## Task 5: Labeled dataset builder

**Files:**
- Create: `learning/meta_dataset.py`
- Test: `tests/test_meta_dataset.py`

Builds one row per tradeable backtest day: `build_features(...)` + label `win` (1 if the realistic trade P&L > 0 else 0). Reuses `SPYBacktest.run()` (regime rows with `date/regime/play/tradeable/adx/vix/ivr/ma200_dist`) and `realistic_pricing.run_realistic_backtest` with `max_concurrent` large so every tradeable day yields a labeled outcome.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_meta_dataset.py
import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.meta_dataset import label_from_pnl, assemble_dataset


def test_label_from_pnl():
    assert label_from_pnl(120.0) == 1
    assert label_from_pnl(-50.0) == 0
    assert label_from_pnl(0.0) == 0


def test_assemble_joins_features_and_labels():
    # Minimal synthetic regime frame + trades frame; assemble must inner-join on date.
    regime = pd.DataFrame([
        {"date": pd.Timestamp("2025-01-10"), "regime": "trending_up_calm",
         "play": "bull_debit", "tradeable": True,
         "adx": 34.0, "vix": 17.0, "ivr": 40.0, "ma200_dist": 9.4, "spy_close": 742.6},
        {"date": pd.Timestamp("2025-01-11"), "regime": "choppy_low_vol",
         "play": "iron_condor", "tradeable": True,
         "adx": 18.0, "vix": 14.0, "ivr": 30.0, "ma200_dist": 2.0, "spy_close": 740.0},
    ])
    trades = pd.DataFrame([
        {"date": pd.Timestamp("2025-01-10"), "pnl_dollars": 150.0},
        {"date": pd.Timestamp("2025-01-11"), "pnl_dollars": -90.0},
    ])
    ds = assemble_dataset(regime, trades, spy_df=None, include_fvg=False)
    assert list(ds["win"]) == [1, 0]
    assert "adx" in ds.columns and "regime_trending_up" in ds.columns
    assert len(ds) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_meta_dataset.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learning.meta_dataset'`

- [ ] **Step 3: Implement `learning/meta_dataset.py`**

```python
"""
learning/meta_dataset.py -- Build the meta-label training set from the backtest.

One row per tradeable day = build_features(...) + label win∈{0,1} from the
realistic per-trade P&L. This is the bootstrap dataset; live recalibration
appends real paper outcomes later (learning/meta_recalibrate.py).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from signals.feature_builder import build_features


def label_from_pnl(pnl: float) -> int:
    """Win label: strictly positive realized P&L."""
    return 1 if pnl > 0 else 0


def assemble_dataset(regime_df: pd.DataFrame, trades_df: pd.DataFrame,
                     spy_df: pd.DataFrame | None = None,
                     include_fvg: bool = False) -> pd.DataFrame:
    """Inner-join regime rows with realistic trade outcomes on date → labeled rows."""
    spy_idx = None
    if spy_df is not None:
        spy_idx = spy_df.copy()
        spy_idx.index = pd.to_datetime(spy_idx.index)

    pnl_by_date = {pd.Timestamp(r["date"]): r["pnl_dollars"]
                   for _, r in trades_df.iterrows()}
    rows = []
    for _, r in regime_df[regime_df["tradeable"] == True].iterrows():
        d = pd.Timestamp(r["date"])
        if d not in pnl_by_date:
            continue
        metrics = {"adx": r["adx"], "vix": r["vix"], "ivr": r["ivr"],
                   "ma200_dist_%": r["ma200_dist"], "spy_close": r.get("spy_close", 0.0)}
        slice_df = None
        if spy_idx is not None:
            slice_df = spy_idx.loc[:d].tail(60)
        feats = build_features(r["regime"], metrics, slice_df, include_fvg)
        feats["date"] = d
        feats["win"] = label_from_pnl(pnl_by_date[d])
        rows.append(feats)
    return pd.DataFrame(rows)


def build_from_history(years: int = 5, include_fvg: bool = False) -> pd.DataFrame:
    """Convenience: load 5yr local data, run both engines, assemble. Used by trainer."""
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from backtests.realistic_pricing import run_realistic_backtest
    from data.event_calendar import EventCalendar

    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=years).run()
    # spy_close column for FVG/feature use (regime_df already has adx/vix/ivr/ma200_dist).
    if "spy_close" not in regime_df.columns:
        closes = {pd.Timestamp(d): float(spy_df.loc[d, "close"]) for d in spy_df.index}
        regime_df["spy_close"] = regime_df["date"].map(lambda x: closes.get(pd.Timestamp(x), 0.0))
    trades_df = run_realistic_backtest(spy_df, regime_df, vix_df, max_concurrent=9999)
    return assemble_dataset(regime_df, trades_df, spy_df, include_fvg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_meta_dataset.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add learning/meta_dataset.py tests/test_meta_dataset.py
git commit -m "feat: meta-label dataset builder (features + win/loss)"
```

---

## Task 6: Trainer + walk-forward ship bar

**Files:**
- Create: `learning/meta_trainer.py`
- Test: `tests/test_meta_trainer.py`

Trains a `Pipeline(StandardScaler, LogisticRegression)`. `walk_forward_eval` does an expanding-window split by date: train on past, score the unseen next slice, aggregate OOS. The **ship bar** (`passes_ship_bar`): OOS win-rate of *taken* trades exceeds the take-everything baseline by ≥ the configured margin, tiers are monotonic OOS, and ≥ 60% of baseline trades are retained (not over-filtering to a tiny sample).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_meta_trainer.py
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.meta_trainer import train_model, score_df, passes_ship_bar


def _learnable_df(n=400, seed=0):
    """High VIX → loss, low VIX → win, with noise. A learnable signal."""
    rng = np.random.default_rng(seed)
    vix = rng.uniform(10, 30, n)
    win = ((30 - vix) / 20 + rng.normal(0, 0.15, n) > 0.5).astype(int)
    return pd.DataFrame({
        "adx": rng.uniform(20, 40, n), "vix": vix, "ivr": rng.uniform(20, 60, n),
        "ma200_dist_pct": rng.uniform(-5, 12, n),
        "regime_trending_up": 1, "regime_trending_down": 0, "regime_choppy_low_vol": 0,
        "win": win,
    })


def test_train_returns_pipeline_that_predicts_proba():
    df = _learnable_df()
    model = train_model(df)
    p = score_df(model, df)
    assert ((p >= 0) & (p <= 1)).all()
    # Low-VIX rows should score higher than high-VIX rows on average.
    lo = p[df["vix"] < 15].mean()
    hi = p[df["vix"] > 25].mean()
    assert lo > hi


def test_ship_bar_passes_on_learnable_and_fails_on_noise():
    good = passes_ship_bar(_learnable_df(seed=1))
    assert good["passes"] is True
    # Pure noise: win independent of features → no OOS edge.
    rng = np.random.default_rng(7)
    noise = _learnable_df(seed=2).copy()
    noise["win"] = rng.integers(0, 2, len(noise))
    bad = passes_ship_bar(noise)
    assert bad["passes"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meta_trainer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learning.meta_trainer'`

- [ ] **Step 3: Implement `learning/meta_trainer.py`**

```python
"""
learning/meta_trainer.py -- Train + walk-forward validate the meta-label model.

Pooled logistic regression with regime one-hot (not per-regime — keeps the
small sample whole). The ship bar is a hard gate: the filter must beat
take-everything out-of-sample, tiers must be monotonic OOS, and it must not
over-filter. Failing the bar means the model does NOT ship (mirrors 0DTE).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import joblib
from loguru import logger
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config
from signals.feature_builder import FEATURE_ORDER, FVG_ORDER

SHIP_WIN_MARGIN   = 0.05   # OOS taken-win-rate must beat baseline by >= 5 pts
SHIP_MIN_RETAIN   = 0.60   # keep >= 60% of baseline trades (no over-filtering)


def _feature_cols(df: pd.DataFrame) -> list[str]:
    cols = list(FEATURE_ORDER)
    if "inside_fvg" in df.columns:
        cols += FVG_ORDER
    return cols


def train_model(df: pd.DataFrame) -> Pipeline:
    """Fit StandardScaler + L2 LogisticRegression on the labeled dataset."""
    cols = _feature_cols(df)
    X, y = df[cols].values, df["win"].values
    model = Pipeline([
        ("scale", StandardScaler()),
        ("lr", LogisticRegression(C=1.0, max_iter=1000)),
    ])
    model.fit(X, y)
    model.feature_cols_ = cols   # stash for scoring parity
    return model


def score_df(model: Pipeline, df: pd.DataFrame) -> pd.Series:
    """P(win) for each row."""
    cols = getattr(model, "feature_cols_", _feature_cols(df))
    return pd.Series(model.predict_proba(df[cols].values)[:, 1], index=df.index)


def _tiers_monotonic(df: pd.DataFrame, proba: pd.Series,
                     cutoffs: dict = None) -> bool:
    """High-tier win-rate >= med-tier win-rate among taken trades."""
    cutoffs = cutoffs or config.META_TIER_CUTOFFS
    taken = proba >= config.META_PROB_THRESHOLD
    if taken.sum() == 0:
        return False
    high = df["win"][proba >= cutoffs["high"]]
    med  = df["win"][(proba >= cutoffs["med"]) & (proba < cutoffs["high"])]
    if len(high) == 0 or len(med) == 0:
        return True  # not enough spread to contradict; don't fail on sparsity
    return high.mean() >= med.mean()


def passes_ship_bar(df: pd.DataFrame, n_folds: int = 4) -> dict:
    """Expanding-window walk-forward. Returns metrics + a boolean `passes`."""
    df = df.reset_index(drop=True)
    fold_size = len(df) // (n_folds + 1)
    oos_rows = []
    for k in range(1, n_folds + 1):
        train = df.iloc[: fold_size * k]
        test  = df.iloc[fold_size * k : fold_size * (k + 1)]
        if len(test) < 10 or train["win"].nunique() < 2:
            continue
        model = train_model(train)
        p = score_df(model, test)
        t = test.copy(); t["proba"] = p.values
        oos_rows.append(t)
    if not oos_rows:
        return {"passes": False, "reason": "insufficient data"}

    oos = pd.concat(oos_rows)
    baseline_win = oos["win"].mean()                     # take everything
    taken = oos["proba"] >= config.META_PROB_THRESHOLD
    taken_win = oos["win"][taken].mean() if taken.sum() else 0.0
    retain = taken.sum() / len(oos)
    monotonic = _tiers_monotonic(oos, oos["proba"])

    passes = (taken_win - baseline_win >= SHIP_WIN_MARGIN
              and retain >= SHIP_MIN_RETAIN and monotonic)
    return {"passes": bool(passes), "baseline_win": round(baseline_win, 3),
            "taken_win": round(taken_win, 3), "retain": round(retain, 3),
            "monotonic": monotonic, "n_oos": len(oos)}


def save_model(model: Pipeline, path: str = None) -> str:
    path = path or config.META_MODEL_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(model, path)
    return path


def main():
    from learning.meta_dataset import build_from_history
    for include_fvg in (False, True):
        df = build_from_history(years=5, include_fvg=include_fvg)
        verdict = passes_ship_bar(df)
        tag = "core+FVG" if include_fvg else "core"
        logger.info(f"[{tag}] n={len(df)} verdict={verdict}")
        print(f"[{tag}] {verdict}")
    # Train + save the CORE model on all data (shipping is still gated by the flag).
    core = build_from_history(years=5, include_fvg=False)
    save_model(train_model(core))
    print(f"saved core model -> {config.META_MODEL_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_meta_trainer.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add learning/meta_trainer.py tests/test_meta_trainer.py
git commit -m "feat: meta-label trainer + walk-forward ship bar"
```

---

## Task 7: Runtime scorer (fail-open)

**Files:**
- Create: `signals/meta_labeler.py`
- Test: `tests/test_meta_labeler.py`

Loads the artifact once; `score(features)` returns `{prob, tier, take}`. If the model is missing or unloadable, it fails OPEN (`take=True, tier=None, prob=None`) so live trading never breaks.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_meta_labeler.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from signals.meta_labeler import MetaLabeler, tier_for
from signals.feature_builder import build_features
from learning.meta_trainer import train_model, save_model
import config

METRICS = {"adx": 34.0, "vix": 17.0, "ivr": 40.0, "ma200_dist_%": 9.4, "spy_close": 742.6}


def test_tier_for_buckets():
    assert tier_for(0.40, config.META_TIER_CUTOFFS) == "skip"
    assert tier_for(0.60, config.META_TIER_CUTOFFS) == "med"
    assert tier_for(0.80, config.META_TIER_CUTOFFS) == "high"


def test_missing_model_fails_open():
    ml = MetaLabeler(path="/nonexistent/model.joblib")
    out = ml.score(build_features("trending_up_calm", METRICS))
    assert out["take"] is True and out["tier"] is None and out["prob"] is None


def test_loaded_model_scores_and_decides(tmp_path):
    import numpy as np
    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame({
        "adx": rng.uniform(20, 40, n), "vix": rng.uniform(10, 30, n),
        "ivr": rng.uniform(20, 60, n), "ma200_dist_pct": rng.uniform(-5, 12, n),
        "regime_trending_up": 1, "regime_trending_down": 0, "regime_choppy_low_vol": 0,
        "win": (rng.uniform(0, 1, n) > 0.5).astype(int),
    })
    p = tmp_path / "m.joblib"
    save_model(train_model(df), str(p))
    ml = MetaLabeler(path=str(p))
    out = ml.score(build_features("trending_up_calm", METRICS))
    assert 0.0 <= out["prob"] <= 1.0
    assert out["take"] == (out["prob"] >= config.META_PROB_THRESHOLD)
    assert out["tier"] in ("skip", "med", "high")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meta_labeler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'signals.meta_labeler'`

- [ ] **Step 3: Implement `signals/meta_labeler.py`**

```python
"""
signals/meta_labeler.py -- Runtime meta-label scoring.

Loads the trained artifact and scores a feature dict → {prob, tier, take}.
Fails OPEN: if the model is missing/unloadable, take=True so the meta-gate can
never block live trading. The gate is additionally guarded by
config.META_LABEL_ENABLED at the call site.
"""

from __future__ import annotations

import os

import joblib
from loguru import logger

import config
from signals.feature_builder import to_vector


def tier_for(prob: float, cutoffs: dict) -> str:
    """skip / med / high from a probability."""
    if prob >= cutoffs["high"]:
        return "high"
    if prob >= config.META_PROB_THRESHOLD:
        return "med"
    return "skip"


class MetaLabeler:
    def __init__(self, path: str = None):
        self.path  = path or config.META_MODEL_PATH
        self.model = None
        if os.path.exists(self.path):
            try:
                self.model = joblib.load(self.path)
            except Exception as e:
                logger.error(f"MetaLabeler load failed ({self.path}): {e}")

    def score(self, features: dict) -> dict:
        if self.model is None:
            return {"prob": None, "tier": None, "take": True}   # fail open
        try:
            cols = getattr(self.model, "feature_cols_", None)
            if cols is not None:
                # Honour the model's exact training column order.
                vec = [float(features[k]) for k in cols]
            else:
                vec = to_vector(features)
            prob = float(self.model.predict_proba([vec])[0][1])
        except Exception as e:
            logger.error(f"MetaLabeler score failed: {e}")
            return {"prob": None, "tier": None, "take": True}   # fail open
        tier = tier_for(prob, config.META_TIER_CUTOFFS)
        return {"prob": round(prob, 3), "tier": tier,
                "take": prob >= config.META_PROB_THRESHOLD}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_meta_labeler.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add signals/meta_labeler.py tests/test_meta_labeler.py
git commit -m "feat: runtime meta-labeler scorer (fail-open)"
```

---

## Task 8: Wire the meta-gate into the daily strategy

**Files:**
- Modify: `signals/spy_daily_strategy.py` (the tradeable branch of `build_today`, around the existing `"tier": "regime_driven"` payload near line 137)
- Test: `tests/test_meta_gate_integration.py`

When `config.META_LABEL_ENABLED` is True and a model is loaded: build features, score; if `take` is False, return the skip card with a meta reason; otherwise stamp the conviction `tier` onto the play. When the flag is False, behaviour is byte-for-byte today's.

- [ ] **Step 1: Read the integration point**

Run: `sed -n '120,170p' signals/spy_daily_strategy.py`
Expected: see the `if not regime_result.tradeable:` skip return, the `spy_close = regime_result.metrics["spy_close"]` line, and the options payload dict containing `"tier": "regime_driven"`.

- [ ] **Step 2: Write the failing integration test**

```python
# tests/test_meta_gate_integration.py
import os, sys
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config
from signals.spy_daily_strategy import SPYDailyStrategy


def _tradeable_regime():
    from signals.regime_detector import RegimeResult, Regime
    return RegimeResult(
        regime=Regime.TRENDING_UP_CALM, tradeable=True, play="Bull debit spread",
        confidence=0.8, reasons=["trend"],
        metrics={"spy_close": 742.6, "ma200": 678.0, "ma200_dist_%": 9.4,
                 "adx": 34.0, "vix": 17.0, "ivr": 40.0},
    )


def test_meta_gate_skips_low_prob_trade():
    strat = SPYDailyStrategy()
    strat.detector = mock.Mock()
    strat.detector.classify.return_value = _tradeable_regime()
    with mock.patch.object(config, "META_LABEL_ENABLED", True), \
         mock.patch("signals.spy_daily_strategy.MetaLabeler") as ML:
        ML.return_value.score.return_value = {"prob": 0.40, "tier": "skip", "take": False}
        card = strat.build_today()
    assert card["tradeable"] is False
    assert "meta" in " ".join(card.get("reasons", [])).lower()


def test_meta_gate_tags_tier_when_taken():
    strat = SPYDailyStrategy()
    strat.detector = mock.Mock()
    strat.detector.classify.return_value = _tradeable_regime()
    strat.options_layer = mock.Mock()
    strat.options_layer.build_spread.return_value = {"legs": [], "tier": "regime_driven"}
    with mock.patch.object(config, "META_LABEL_ENABLED", True), \
         mock.patch("signals.spy_daily_strategy.MetaLabeler") as ML:
        ML.return_value.score.return_value = {"prob": 0.80, "tier": "high", "take": True}
        card = strat.build_today()
    assert card["tradeable"] is True
    assert card.get("meta_tier") == "high"


def test_flag_off_is_noop():
    strat = SPYDailyStrategy()
    strat.detector = mock.Mock()
    strat.detector.classify.return_value = _tradeable_regime()
    strat.options_layer = mock.Mock()
    strat.options_layer.build_spread.return_value = {"legs": [], "tier": "regime_driven"}
    with mock.patch.object(config, "META_LABEL_ENABLED", False):
        card = strat.build_today()
    assert card["tradeable"] is True
    assert card.get("meta_tier") in (None, "regime_driven")
```

> NOTE for the implementer: in Step 1 you may find the options structure is built via a method other than `options_layer.build_spread` (e.g. an internal helper). Adjust the mock target in the second/third tests to match the actual method name you see — keep the assertions identical.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meta_gate_integration.py -v`
Expected: FAIL — `meta_tier`/meta reason not present (gate not wired).

- [ ] **Step 4: Wire the gate into `build_today`**

At the top of `signals/spy_daily_strategy.py`, add the imports:

```python
import config
from signals.feature_builder import build_features
from signals.meta_labeler import MetaLabeler
```

In `build_today`, immediately AFTER the `if not regime_result.tradeable:` skip-return block and BEFORE building the options payload, insert:

```python
        # ── Meta-label gate (secondary take/skip + conviction) ──
        # Inert unless explicitly enabled; fails open if no model is loaded.
        meta_tier = None
        if config.META_LABEL_ENABLED:
            feats = build_features(regime_result.regime.value,
                                   regime_result.metrics)
            decision = MetaLabeler().score(feats)
            if not decision["take"]:
                reason = (f"meta-filter: P(win) {decision['prob']} "
                          f"< {config.META_PROB_THRESHOLD}")
                rr = regime_result
                rr.reasons = list(rr.reasons) + [reason]
                return asdict(self._skip_card(today, rr, track_name))
            meta_tier = decision["tier"]
```

Then, where the returned `PlayCard`/dict is assembled, add the field `meta_tier=meta_tier` (PlayCard) or `"meta_tier": meta_tier` (dict). If `PlayCard` is a dataclass, add `meta_tier: str | None = None` to its field list.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_meta_gate_integration.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full suite (cross-module change)**

Run: `.venv/bin/python -m pytest tests/ -v -m "not integration" --tb=short`
Expected: all pass (no regressions in existing strategy/scanner tests).

- [ ] **Step 7: Commit**

```bash
git add signals/spy_daily_strategy.py tests/test_meta_gate_integration.py
git commit -m "feat: wire meta-label gate into daily strategy (default off)"
```

---

## Task 9: Live recalibration job

**Files:**
- Create: `learning/meta_recalibrate.py`
- Modify: `learning/scheduler.py` (register the job in `register_learning_jobs`)
- Test: `tests/test_meta_recalibrate.py`

Weekly: read resolved paper-trade outcomes from the journal/prediction log, turn each into a labeled row via `build_features` + `label_from_pnl`, append to the bootstrap dataset, refit, re-run `passes_ship_bar`, and **only overwrite the live artifact if it still passes** — otherwise keep the old model and log a warning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_meta_recalibrate.py
import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.meta_recalibrate import recalibrate


def _good_dataset(n=400, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    vix = rng.uniform(10, 30, n)
    win = ((30 - vix) / 20 + rng.normal(0, 0.15, n) > 0.5).astype(int)
    return pd.DataFrame({
        "adx": rng.uniform(20, 40, n), "vix": vix, "ivr": rng.uniform(20, 60, n),
        "ma200_dist_pct": rng.uniform(-5, 12, n),
        "regime_trending_up": 1, "regime_trending_down": 0, "regime_choppy_low_vol": 0,
        "win": win,
    })


def test_recalibrate_writes_when_passes(tmp_path):
    path = tmp_path / "m.joblib"
    res = recalibrate(dataset=_good_dataset(seed=1), model_path=str(path))
    assert res["passed"] is True
    assert path.exists()


def test_recalibrate_keeps_old_model_when_fails(tmp_path):
    import numpy as np
    path = tmp_path / "m.joblib"
    path.write_bytes(b"OLD")           # pretend an existing model is here
    noise = _good_dataset(seed=2).copy()
    noise["win"] = np.random.default_rng(3).integers(0, 2, len(noise))
    res = recalibrate(dataset=noise, model_path=str(path))
    assert res["passed"] is False
    assert path.read_bytes() == b"OLD"  # untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meta_recalibrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learning.meta_recalibrate'`

- [ ] **Step 3: Implement `learning/meta_recalibrate.py`**

```python
"""
learning/meta_recalibrate.py -- Periodic live recalibration of the meta-model.

Refits on the bootstrap dataset + accumulated live paper outcomes and ONLY
swaps the live artifact if the refit still clears the walk-forward ship bar.
A failing refit leaves the existing model untouched (and logs it).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config
from learning.meta_trainer import train_model, save_model, passes_ship_bar


def recalibrate(dataset: pd.DataFrame = None, model_path: str = None) -> dict:
    """Refit + ship-bar check. Writes the model only on pass. Returns the verdict."""
    model_path = model_path or config.META_MODEL_PATH
    if dataset is None:
        from learning.meta_dataset import build_from_history
        dataset = build_from_history(years=5, include_fvg=False)
        dataset = _append_live_outcomes(dataset)

    verdict = passes_ship_bar(dataset)
    if verdict.get("passes"):
        save_model(train_model(dataset), model_path)
        logger.info(f"meta-model recalibrated + saved: {verdict}")
        return {"passed": True, "verdict": verdict}
    logger.warning(f"meta-model recalibration did NOT pass ship bar; kept old: {verdict}")
    return {"passed": False, "verdict": verdict}


def _append_live_outcomes(dataset: pd.DataFrame) -> pd.DataFrame:
    """Append resolved paper-trade outcomes as labeled rows. Best-effort: if the
    journal is unavailable or empty, return the dataset unchanged."""
    try:
        from journal.trade_recorder import TradeRecorder  # adjust if API differs
        rows = TradeRecorder().resolved_meta_rows()  # expected: list[feature+win dicts]
        if rows:
            return pd.concat([dataset, pd.DataFrame(rows)], ignore_index=True)
    except Exception as e:
        logger.debug(f"No live outcomes appended: {e}")
    return dataset


def run_meta_recalibration():
    """Scheduler entry point (wrapped in try/except per standing rule 10)."""
    try:
        recalibrate()
    except Exception as e:
        logger.error(f"meta recalibration job failed: {e}")
```

> NOTE for the implementer: `_append_live_outcomes` assumes a `resolved_meta_rows()` helper on the journal that returns feature+win dicts. If that helper does not exist yet, leave the best-effort try/except as-is (it degrades to "backtest only") and add the journal helper in a follow-up task — do NOT block this task on it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_meta_recalibrate.py -v`
Expected: 2 passed.

- [ ] **Step 5: Register the job in the learning scheduler**

Read `learning/scheduler.py` and find `register_learning_jobs`. Add, following the existing `scheduler.add_job(...)` pattern (cron, timezone=US/Eastern, wrapped function):

```python
    from learning.meta_recalibrate import run_meta_recalibration
    scheduler.add_job(
        run_meta_recalibration,
        "cron", day_of_week="sat", hour=12, minute=0,
        timezone="US/Eastern", id="meta_recalibration",
        replace_existing=True,
    )
```

- [ ] **Step 6: Verify the scheduler still imports**

Run: `.venv/bin/python -c "import learning.scheduler; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add learning/meta_recalibrate.py learning/scheduler.py tests/test_meta_recalibrate.py
git commit -m "feat: weekly meta-model recalibration job (ship-bar gated)"
```

---

## Task 10: Generate the bootstrap model + record the verdict

**Files:**
- Run: `learning/meta_trainer.py` (no code changes)
- Modify: `BUILD_LOG.md`

- [ ] **Step 1: Train + print the walk-forward verdict on real data**

Run: `.venv/bin/python -m learning.meta_trainer`
Expected: prints `[core] {...}` and `[core+FVG] {...}` verdicts and `saved core model -> logs/learning/meta_model.joblib`.

- [ ] **Step 2: Record the honest result**

Whatever the verdict — **even if it does NOT pass** — append a `BUILD_LOG.md` entry under today's date stating: the core verdict (baseline_win vs taken_win, retain, monotonic), the core+FVG verdict, and whether FVG beat core. If neither passes, the entry says the meta-layer stays shelved (flag off) — that is a valid, expected outcome, not a failure of the work.

- [ ] **Step 3: Confirm the gate stays inert**

Verify `config.META_LABEL_ENABLED` is still `False`. The model artifact existing on disk must NOT enable the gate — only the flag does. Promotion to `True` is a deliberate, separate human decision after reviewing the verdict.

- [ ] **Step 4: Commit**

```bash
git add BUILD_LOG.md
git commit -m "docs: BUILD_LOG — meta-label bootstrap walk-forward verdict"
```

> The model artifact at `logs/learning/meta_model.joblib` is NOT committed — `logs/` is gitignored. It is regenerated by running the trainer.

---

## Self-Review Notes

- **Spec coverage:** feature_builder parity (T3), fvg (T2), trainer+walk-forward ship bar (T6), runtime scorer fail-open (T7), gate integration + no-op safety (T8), recalibration via learning loop (T9), config flags default-off (T4), bootstrap-then-recalibrate labels (T5/T9), FVG-as-experiment delta (T6 `main` runs core vs core+FVG), confidence tiers no-sizing (T7/T8). All spec sections map to a task.
- **Placeholders:** none — every code step has full code; the two implementer NOTES (options method name in T8, journal helper in T9) describe concrete fallbacks, not deferred work.
- **Type consistency:** `build_features(regime, metrics, spy_df, include_fvg)`, `to_vector`, `FEATURE_ORDER`/`FVG_ORDER`, `train_model`/`score_df`/`passes_ship_bar`/`save_model`, `MetaLabeler.score → {prob,tier,take}`, `tier_for` all used consistently across T3–T9.
- **Dependency order:** T1(deps) → T2/T3(features) → T4(config) → T5(dataset) → T6(trainer) → T7(scorer) → T8(integration) → T9(recalibration) → T10(run). Each task is independently testable.
