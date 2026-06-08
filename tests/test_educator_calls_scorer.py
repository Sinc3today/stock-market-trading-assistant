"""tests/test_educator_calls_scorer.py -- score educators' dated forward CALLS
against our own price history. Pure-function tests (no file IO, no network)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


SAMPLE_KB = """\

## PCE & ALL TIME HIGHS
<!--vid:abc123 date:2025-06-27--> (33075 words)

1) CALLS
CALL | SPY | up | 1-2wk | 620 | breadth strong, dips bought
CALL | VIX | down | days | - | uncertainty resolving post-PCE
CALLS captured above.

2) MARKET READ
Extended but resilient; dips keep getting bought.

3) NON-PRICE SIGNALS
sentiment, Fed/liquidity, options gamma

## A QUIET FRIDAY
<!--vid:def456 date:2025-03-14--> (5000 words)

1) CALLS: none

2) MARKET READ
Range-bound, low conviction.
"""


def test_parse_kb_extracts_dated_calls():
    from backtests.educator_calls_scorer import parse_kb
    calls = parse_kb(SAMPLE_KB)
    assert len(calls) == 2                      # the "none" block contributes no calls
    c0 = calls[0]
    assert c0["date"] == "2025-06-27"
    assert c0["instrument"] == "SPY"
    assert c0["direction"] == "up"
    assert c0["horizon"] == "1-2wk"
    assert c0["title"].startswith("PCE")
    assert calls[1]["instrument"] == "VIX"


def test_parse_kb_skips_template_echo_lines():
    from backtests.educator_calls_scorer import parse_kb
    # small models sometimes echo the schema instead of filling it — must be dropped
    kb = """
## Echoed Schema Video
<!--vid:zzz date:2025-01-02--> (100 words)
CALL | INSTRUMENT | DIRECTION | HORIZON | LEVEL | REASONING
CALL | instrument (SPY/QQQ/VIX) | direction (up/down) | horizon | - | x
CALL | SPY | up | days | - | a real one
"""
    calls = parse_kb(kb)
    assert len(calls) == 1                 # only the real filled-in line survives
    assert calls[0]["instrument"] == "SPY"


def test_horizon_to_days():
    from backtests.educator_calls_scorer import horizon_to_days
    assert horizon_to_days("today") == 1
    assert horizon_to_days("days") == 5
    assert horizon_to_days("1-2wk") == 10
    assert horizon_to_days("weeks") == 15
    assert horizon_to_days("months") == 30
    assert horizon_to_days("garbage") == 5       # default = a trading week


def _rising_df(start="2025-06-27", n=40, step=1.0):
    idx = pd.bdate_range(start, periods=n)
    close = pd.Series([100 + i * step for i in range(n)], index=idx, dtype=float)
    return pd.DataFrame({"close": close})


def test_score_call_up_hit_on_rising_market():
    from backtests.educator_calls_scorer import score_call
    call = {"date": "2025-06-27", "instrument": "SPY", "direction": "up", "horizon": "1-2wk"}
    v = score_call(call, _rising_df())
    assert v["verdict"] == "hit"
    assert v["fwd_ret"] > 0


def test_score_call_down_miss_on_rising_market():
    from backtests.educator_calls_scorer import score_call
    call = {"date": "2025-06-27", "instrument": "SPY", "direction": "down", "horizon": "days"}
    v = score_call(call, _rising_df())
    assert v["verdict"] == "miss"


def test_score_call_out_of_range_is_unscored():
    from backtests.educator_calls_scorer import score_call
    call = {"date": "2030-01-01", "instrument": "SPY", "direction": "up", "horizon": "days"}
    v = score_call(call, _rising_df())
    assert v["verdict"] == "unscored"


def test_aggregate_hit_rate():
    from backtests.educator_calls_scorer import aggregate
    scored = [
        {"verdict": "hit"}, {"verdict": "hit"}, {"verdict": "miss"},
        {"verdict": "unscored"}, {"verdict": "flat"},
    ]
    agg = aggregate(scored)
    assert agg["scored"] == 4          # excludes 'unscored'
    assert agg["hits"] == 2
    assert agg["hit_rate"] == 50.0     # 2 hits of 4 scored
