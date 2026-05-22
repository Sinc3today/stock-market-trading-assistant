"""
signals/meta_labeler.py -- Runtime meta-label scoring.

Loads the trained artifact and scores a feature dict -> {prob, tier, take}.
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
