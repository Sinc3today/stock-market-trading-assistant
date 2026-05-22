import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indicators.fvg import detect_fvgs, fvg_features


def _df(rows):
    # rows: list of (high, low). close/open not needed for gap geometry.
    return pd.DataFrame({"high": [r[0] for r in rows], "low": [r[1] for r in rows]})


def test_detects_bullish_gap():
    # candle 0 high=100; candle 1 surges; candle 2 low=102 > candle 0 high=100 -> gap (100,102)
    df = _df([(100, 95), (108, 101), (110, 102)])
    gaps = detect_fvgs(df)
    assert any(g["type"] == "bull" and g["bottom"] == 100 and g["top"] == 102 for g in gaps)


def test_detects_bearish_gap():
    # candle 2 high=98 < candle 0 low=100 -> gap (98,100)
    df = _df([(105, 100), (99, 92), (98, 90)])
    gaps = detect_fvgs(df)
    assert any(g["type"] == "bear" and g["bottom"] == 98 and g["top"] == 100 for g in gaps)


def test_filled_gap_excluded():
    # bullish gap (100,102) at i=2, then candle 3 trades back down through it -> filled
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
