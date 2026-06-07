"""tests/test_dipbuy_wf.py -- expanding-window OOS confirmation for the dip-buy.

Parameter-free rule, so the WF has nothing to FIT — it confirms temporal
robustness by burning in the first N trade-years as "train" and aggregating
every later year as out-of-sample, then applying the project's standard gates.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def _t(year, pnl):
    return {"entry_year": year, "pnl_dollars": pnl}


def test_expanding_oos_folds_burn_in_first_years():
    from backtests.dipbuy_wf import expanding_oos_folds
    trades = [_t(2010, 10), _t(2011, 20), _t(2012, 30), _t(2013, 40), _t(2014, 50)]
    folds = expanding_oos_folds(trades, min_train_years=2)
    # first 2 distinct years (2010, 2011) are train; OOS = 2012, 2013, 2014
    assert set(folds.keys()) == {2012, 2013, 2014}
    assert folds[2012] == [30]


def test_oos_metrics_pool_and_sharpe():
    from backtests.dipbuy_wf import oos_metrics
    folds = {2012: [30.0, 10.0], 2013: [-20.0], 2014: [40.0]}
    m = oos_metrics(folds)
    assert m["n"] == 4
    assert m["mean_pnl"] == pytest.approx(15.0)
    assert m["win_rate"] == pytest.approx(0.75)        # 3 of 4 positive
    assert m["sharpe"] == pytest.approx(15.0 / (525.0 ** 0.5), abs=1e-2)  # mean/std
    assert m["pos_year_frac"] == pytest.approx(2 / 3, abs=1e-2)  # 2012,2014 positive; 2013 not


def test_wf_verdict_passes_on_positive_robust_oos():
    from backtests.dipbuy_wf import wf_verdict
    m = {"n": 25, "mean_pnl": 90.0, "win_rate": 0.66, "sharpe": 0.4,
         "pos_year_frac": 0.8}
    v = wf_verdict(m)
    assert v["passes"] is True


def test_wf_verdict_fails_on_negative_oos_mean():
    from backtests.dipbuy_wf import wf_verdict
    m = {"n": 25, "mean_pnl": -5.0, "win_rate": 0.66, "sharpe": -0.1,
         "pos_year_frac": 0.8}
    v = wf_verdict(m)
    assert v["passes"] is False
