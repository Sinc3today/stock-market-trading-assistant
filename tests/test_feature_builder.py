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
    a = to_vector(build_features("trending_up_calm", METRICS))
    b = to_vector(build_features("trending_up_calm", dict(METRICS)))
    assert a == b
