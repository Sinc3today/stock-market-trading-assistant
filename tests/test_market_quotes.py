"""tests/test_market_quotes.py -- live mark-to-market from real NBBO quotes (pure).

The bot marks positions at day-close/vwap (optimistic). Given real bid/ask per
leg (from yfinance — the same NBBO RH shows), position_mtm computes what the
structure is really worth now and the spread you'd cross to close. The yfinance
fetch is isolated; this tests the math against the user's actual 06-22 condor.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def _condor_quotes():
    # ~ the real 7/24 SPY chain for the user's condor (771/776 call, 700/695 put)
    return [
        {"action": "SELL", "option_type": "CALL", "strike": 771, "bid": 0.80, "ask": 0.86, "mid": 0.83},
        {"action": "BUY",  "option_type": "CALL", "strike": 776, "bid": 0.43, "ask": 0.49, "mid": 0.46},
        {"action": "SELL", "option_type": "PUT",  "strike": 700, "bid": 3.85, "ask": 3.95, "mid": 3.90},
        {"action": "BUY",  "option_type": "PUT",  "strike": 695, "bid": 3.27, "ask": 3.37, "mid": 3.32},
    ]


def test_position_mtm_credit_condor():
    from data.market_quotes import position_mtm
    m = position_mtm(_condor_quotes(), entry_price=1.55, size=2, action="credit")
    # current value at mid = longs(0.46+3.32) - shorts(0.83+3.90) = -0.95
    assert round(m["current_value_mid"], 2) == -0.95
    # MTM = (current - open(-1.55)) * size * 100 = 0.60 * 200 = +120
    assert round(m["mtm_dollars"], 2) == 120.00
    # crossing the spread to close costs ~0.16/share -> ~$32 on 2 contracts
    assert round(m["spread_cost_dollars"], 2) == 32.00


def test_position_mtm_debit_spread():
    from data.market_quotes import position_mtm
    legs = [
        {"action": "BUY",  "option_type": "CALL", "strike": 700, "bid": 7.9, "ask": 8.1, "mid": 8.0},
        {"action": "SELL", "option_type": "CALL", "strike": 705, "bid": 4.9, "ask": 5.1, "mid": 5.0},
    ]
    m = position_mtm(legs, entry_price=2.00, size=1, action="debit")
    # current value at mid = long 8.0 - short 5.0 = 3.0; MTM = (3.0 - 2.0)*100 = +100
    assert round(m["current_value_mid"], 2) == 3.00
    assert round(m["mtm_dollars"], 2) == 100.00
    assert round(m["spread_cost_dollars"], 2) == 20.00


def test_position_mtm_none_without_quotes():
    from data.market_quotes import position_mtm
    # a leg missing a mid -> can't mark -> None
    legs = [{"action": "SELL", "option_type": "CALL", "strike": 771, "mid": None}]
    assert position_mtm(legs, entry_price=1.0, size=1, action="credit") is None
