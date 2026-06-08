"""tests/test_dipbuy_horizon_sweep.py -- DTE horizon sweep of the oversold dip-buy."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd


def _frame(closes, start="2015-01-02"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 1.0, "low": c - 1.0,
                         "close": c, "volume": 1_000_000})


def test_sweep_returns_metrics_per_dte():
    from backtests.dipbuy_horizon_sweep import sweep_horizons
    closes = [400.0] * 30 + list(np.linspace(400, 440, 40))  # rally after trigger
    df = _frame(closes)
    trig = pd.Series(False, index=df.index); trig.iloc[30] = True
    vix_at = {d: 20.0 for d in df.index}
    res = sweep_horizons(df, vix_at, trig, dtes=(5, 21))
    assert set(res.keys()) == {5, 21}
    for d in (5, 21):
        assert {"n", "mean_pnl", "win_rate", "total_pnl", "target_frac"} <= set(res[d])
        assert res[d]["n"] == 1


def test_sweep_rally_is_profitable_at_longer_dte():
    from backtests.dipbuy_horizon_sweep import sweep_horizons
    closes = [400.0] * 30 + list(np.linspace(400, 445, 40))
    df = _frame(closes)
    trig = pd.Series(False, index=df.index); trig.iloc[30] = True
    vix_at = {d: 20.0 for d in df.index}
    res = sweep_horizons(df, vix_at, trig, dtes=(21,))
    assert res[21]["mean_pnl"] > 0   # bull debit into a rally with time = profit
