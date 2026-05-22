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
