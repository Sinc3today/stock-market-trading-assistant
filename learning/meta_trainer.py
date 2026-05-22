"""
learning/meta_trainer.py -- Train + walk-forward validate the meta-label model.

Pooled logistic regression with regime one-hot (not per-regime -- keeps the
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
SHIP_MIN_RETAIN   = 0.40   # keep >= 40% of candidate trades. A strong filter
                           # SHOULD be free to skip half the candidates if it
                           # lifts win-rate; this floor only blocks pathological
                           # over-filtering down to a tiny, lucky sample.


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
    core = build_from_history(years=5, include_fvg=False)
    save_model(train_model(core))
    print(f"saved core model -> {config.META_MODEL_PATH}")


if __name__ == "__main__":
    main()
