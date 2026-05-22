import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.meta_trainer import train_model, score_df, passes_ship_bar


def _learnable_df(n=400, seed=0):
    """High VIX -> loss, low VIX -> win, with noise. A learnable signal."""
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
    lo = p[df["vix"] < 15].mean()
    hi = p[df["vix"] > 25].mean()
    assert lo > hi


def test_ship_bar_passes_on_learnable_and_fails_on_noise():
    good = passes_ship_bar(_learnable_df(seed=1))
    assert good["passes"] is True
    rng = np.random.default_rng(7)
    noise = _learnable_df(seed=2).copy()
    noise["win"] = rng.integers(0, 2, len(noise))
    bad = passes_ship_bar(noise)
    assert bad["passes"] is False
