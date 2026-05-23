import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_intraday_touch_ship_bar_constants_present():
    # These are the three binding floors for the default-2σ preset in the
    # walk-forward harness. Other presets are hard-coded in the harness itself.
    assert config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR == 25.0
    assert config.INTRADAY_TOUCH_SHIP_MIN_FRAC   == 0.10
    assert config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB == 0.15
