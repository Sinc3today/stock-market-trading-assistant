"""tests/test_dipbuy_option_wf.py -- Phase 2 option-priced dip-buy walk-forward."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
import pytest


def _frame(closes, start="2015-01-02"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 1.0, "low": c - 1.0,
                         "close": c, "volume": 1_000_000})


def test_price_dip_trades_profits_on_rally():
    from backtests.dipbuy_option_wf import price_dip_trades
    closes = [400.0] * 30 + list(np.linspace(400, 448, 40))   # rally after idx 30
    df = _frame(closes)
    trig = pd.Series(False, index=df.index)
    trig.iloc[30] = True
    vix_at = {d: 20.0 for d in df.index}
    trades = price_dip_trades(df, vix_at, trig)
    assert len(trades) == 1
    assert trades[0]["pnl_dollars"] > 0          # bull debit into a rally wins
    assert trades[0]["entry_year"] == df.index[30].year


def test_stress_reduces_pnl():
    from backtests.dipbuy_option_wf import price_dip_trades
    closes = [400.0] * 30 + list(np.linspace(400, 448, 40))
    df = _frame(closes)
    trig = pd.Series(False, index=df.index); trig.iloc[30] = True
    vix_at = {d: 20.0 for d in df.index}
    face = price_dip_trades(df, vix_at, trig, stress_mult=1.0)[0]["pnl_dollars"]
    stressed = price_dip_trades(df, vix_at, trig, stress_mult=1.25)[0]["pnl_dollars"]
    assert stressed < face                       # higher entry IV = pricier debit


def test_summarize_per_year_and_halves():
    from backtests.dipbuy_option_wf import summarize
    trades = [
        {"entry_year": 2015, "pnl_dollars": 50.0},
        {"entry_year": 2015, "pnl_dollars": -10.0},
        {"entry_year": 2016, "pnl_dollars": 30.0},
        {"entry_year": 2017, "pnl_dollars": 20.0},
    ]
    s = summarize(trades)
    assert s["n"] == 4
    assert s["mean_pnl"] == pytest.approx(22.5)
    assert s["per_year"][2015] == pytest.approx(20.0)
    assert s["half_means"][0] > 0 and s["half_means"][1] > 0


def test_phase2_verdict_passes_when_face_and_stress_positive():
    from backtests.dipbuy_option_wf import phase2_verdict
    face = {"n": 30, "mean_pnl": 25.0, "per_year": {y: 10.0 for y in range(2015, 2021)},
            "half_means": (20.0, 30.0)}
    stressed = {"n": 30, "mean_pnl": 8.0}
    v = phase2_verdict(face, stressed)
    assert v["survives"] is True


def test_phase2_verdict_fails_when_stress_kills_edge():
    from backtests.dipbuy_option_wf import phase2_verdict
    face = {"n": 30, "mean_pnl": 25.0, "per_year": {y: 10.0 for y in range(2015, 2021)},
            "half_means": (20.0, 30.0)}
    stressed = {"n": 30, "mean_pnl": -5.0}     # edge evaporates under IV stress
    v = phase2_verdict(face, stressed)
    assert v["survives"] is False
