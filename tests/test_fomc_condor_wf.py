"""tests/test_fomc_condor_wf.py -- defined-risk iron condor sold INTO FOMC.

Tests whether selling a condor at the expected-move breakevens and holding to
expiry profits from FOMC's over-priced premium (the event_straddle lead).
Pure pricing-logic tests; the real option-price pull runs in main().
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_condor_strikes_at_expected_move():
    from backtests.fomc_condor_wf import condor_strikes
    lp, sp, sc, lc = condor_strikes(spot=600.0, expected_move=10.0, width=5)
    assert (lp, sp, sc, lc) == (585, 590, 610, 615)


def test_condor_credit_is_shorts_minus_wings():
    from backtests.fomc_condor_wf import condor_credit
    # sell short put 2.00 + short call 2.00, buy wings 0.50 each -> credit 3.00
    assert condor_credit(sp_close=2.0, lp_close=0.5, sc_close=2.0, lc_close=0.5) == 3.0


def test_expiry_liability_zero_inside_shorts():
    from backtests.fomc_condor_wf import condor_expiry_liability
    # exit between the short strikes -> everything expires worthless
    assert condor_expiry_liability(585, 590, 610, 615, exit_spot=600.0) == 0.0


def test_expiry_liability_capped_at_width_when_breached():
    from backtests.fomc_condor_wf import condor_expiry_liability
    # exit far below long put -> full put-spread width (5), call side worthless
    assert condor_expiry_liability(585, 590, 610, 615, exit_spot=560.0) == 5.0
    # partial: exit between short_put(590) and long_put(585)
    assert condor_expiry_liability(585, 590, 610, 615, exit_spot=588.0) == 2.0


def test_condor_pnl_max_profit_and_max_loss():
    from backtests.fomc_condor_wf import condor_pnl
    # credit 3, width 5, 1 contract x100
    assert condor_pnl(3.0, 585, 590, 610, 615, exit_spot=600.0) == 300.0   # full credit
    assert condor_pnl(3.0, 585, 590, 610, 615, exit_spot=560.0) == -200.0  # (3-5)*100


def test_summarize_winrate_and_expectancy():
    from backtests.fomc_condor_wf import summarize
    s = summarize([300.0, 300.0, -200.0, 300.0])   # 3 wins, 1 loss
    assert s["n"] == 4
    assert s["win_rate"] == 75.0
    assert s["mean"] == pytest.approx(175.0)
    assert s["total"] == 700.0
