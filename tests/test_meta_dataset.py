import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.meta_dataset import label_from_pnl, assemble_dataset


def test_label_from_pnl():
    assert label_from_pnl(120.0) == 1
    assert label_from_pnl(-50.0) == 0
    assert label_from_pnl(0.0) == 0


def test_assemble_joins_features_and_labels():
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
