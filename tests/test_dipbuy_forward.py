"""tests/test_dipbuy_forward.py -- live forward paper-test of the oversold dip-buy."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest


def test_config_has_dipbuy_forward_flags():
    import config
    assert config.DIPBUY_FORWARD_ENABLED is True
    assert config.DIPBUY_FORWARD_DTE == 21
    assert config.DIPBUY_FORWARD_TARGET_PCT == 0.50
    assert config.DIPBUY_FORWARD_MAX_HOLD_TD == 10
