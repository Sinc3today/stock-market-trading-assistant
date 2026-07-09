"""tests/test_hypothesis_guard.py -- multiple-testing guards (audit T3#11)."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from learning.hypothesis_runner import (
    SHARPE_ACCEPT_DELTA, oos_rotation, split_is_oos, N_OOS_ROTATIONS,
)


def test_acceptance_bar_raised():
    assert SHARPE_ACCEPT_DELTA >= 0.15


def test_rotation_cycles_with_week():
    rots = {oos_rotation(date(2026, 7, d)) for d in (4, 11, 18)}  # 3 consecutive Saturdays
    assert rots == set(range(N_OOS_ROTATIONS))                    # all layouts used


def test_split_layouts_are_60_40_and_chronological_oos():
    df = pd.DataFrame({"x": range(100)})
    for rot in range(N_OOS_ROTATIONS):
        is_, oos = split_is_oos(df, rot)
        assert len(oos) == 40 and len(is_) == 60
        # OOS is one contiguous chronological block
        idx = list(oos.index)
        assert idx == list(range(idx[0], idx[0] + 40))
    # and the three layouts test DIFFERENT data
    blocks = {tuple(split_is_oos(df, r)[1].index[:1]) for r in range(N_OOS_ROTATIONS)}
    assert len(blocks) == N_OOS_ROTATIONS
