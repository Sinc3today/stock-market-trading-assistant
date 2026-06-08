"""tests/test_event_timing_study.py -- event-relative timing (buy rumor / sell news)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_nth_friday():
    from backtests.event_timing_study import _nth_friday
    # Jan 2024: Fridays are 5,12,19,26 → 1st=5th, 3rd=19th
    assert _nth_friday(2024, 1, 1) == pd.Timestamp("2024-01-05")
    assert _nth_friday(2024, 1, 3) == pd.Timestamp("2024-01-19")


def test_event_dates_align_to_trading_days():
    from backtests.event_timing_study import event_dates
    idx = pd.bdate_range("2024-01-01", "2024-03-31")
    nfp = event_dates(idx, kind="nfp")
    # Jan/Feb/Mar 2024 first Fridays: 1/5, 2/2, 3/1 — all weekdays, in index
    assert pd.Timestamp("2024-01-05") in nfp
    assert pd.Timestamp("2024-02-02") in nfp


def test_event_window_returns_pre_post():
    from backtests.event_timing_study import event_window_returns
    idx = pd.bdate_range("2024-01-01", periods=20)
    close = pd.Series([100, 101, 102, 103, 104, 110, 108, 106, 104, 102,
                       100, 99, 98, 97, 96, 95, 94, 93, 92, 91],
                      index=idx, dtype=float)
    df = pd.DataFrame({"close": close})
    # event at idx[5] (110): pre = 110/103-1 (run-up), post = 102/110-1 (reversal)
    res = event_window_returns(df, [idx[5]], pre=3, post=3)
    assert res["n"] == 1
    assert res["pre_mean"] > 0      # ran up into the event
    assert res["post_mean"] < 0     # reversed after
