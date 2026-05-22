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
