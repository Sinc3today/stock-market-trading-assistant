"""tests/test_structure_minutes.py -- value a real option structure, minute by minute.

The one place that turns Polygon's historical option bars into "what was this
spread worth at 14:06 ET". Two things made it necessary:

  * OptionsHistory.get_aggs indexes bars in naive UTC. PolygonClient indexes
    in naive HOST-LOCAL time (Chicago). A "14:05 entry" read against the wrong
    one is an hour or four off. The conversion lives here, once.
  * The sandbox's recorded entry prices came from a model: before the
    2026-09-06 fix, 13 of 22 same-day debit spreads were recorded more than
    25% away from the real last trade (86607E44: recorded $0.17, real $0.84).
    Replaying against real bars is how those records get re-measured, and it
    is also how the event-day shadow trades get priced.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

import pandas as pd
import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data import options_history as oh

ET = pytz.timezone("US/Eastern")


def _utc_bars(day: date, prices_by_et_minute: dict[str, float]) -> pd.DataFrame:
    """Minute bars as Polygon returns them: naive UTC index."""
    rows = {}
    for hhmm, px in prices_by_et_minute.items():
        h, m = (int(x) for x in hhmm.split(":"))
        ts = ET.localize(datetime(day.year, day.month, day.day, h, m))
        rows[ts.astimezone(pytz.utc).replace(tzinfo=None)] = px
    idx = pd.DatetimeIndex(sorted(rows), name="timestamp")
    close = [rows[i] for i in idx]
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1}, index=idx)


class _FakeHistory(oh.OptionsHistory):
    def __init__(self, bars: dict[str, pd.DataFrame]):
        self._bars = bars

    def get_aggs(self, contract, multiplier, timespan, from_date, to_date,
                 limit=50000, use_cache=True):
        return self._bars.get(contract, pd.DataFrame(columns=oh._COLS))


D = date(2026, 9, 16)
EXP = date(2026, 9, 17)


def _et(hhmm: str, day: date = D) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return ET.localize(datetime(day.year, day.month, day.day, h, m))


def _legs(k_long=700.0, k_short=705.0, cp="call", buy="BUY", sell="SELL"):
    return [{"action": buy, "option_type": cp, "strike": k_long, "expiration": EXP.isoformat()},
            {"action": sell, "option_type": cp, "strike": k_short, "expiration": EXP.isoformat()}]


def _tk(k, cp="C"):
    return oh.option_ticker("SPY", EXP, cp, k)


# ── timezone: one conversion, correct in both seasons ─────────────

def test_summer_utc_bar_lands_at_the_eastern_open():
    df = _utc_bars(D, {"09:30": 1.0})
    out = oh.to_eastern(df)
    assert out.index[0].hour == 9 and out.index[0].minute == 30
    assert str(out.index.tz) == "US/Eastern"


def test_winter_utc_bar_lands_at_the_eastern_open():
    """EST is UTC-5, EDT is UTC-4. A fixed offset would be wrong half the year."""
    winter = date(2026, 1, 15)
    df = _utc_bars(winter, {"09:30": 1.0})
    assert df.index[0].hour == 14                      # stored as 14:30 UTC
    assert oh.to_eastern(df).index[0].hour == 9


def test_the_backtest_helper_uses_the_same_conversion():
    """Two conversions is how the units bugs started."""
    from backtests.intraday_backtest import _to_et
    df = _utc_bars(D, {"09:30": 1.0, "14:06": 2.0})
    assert list(_to_et(df).index) == list(oh.to_eastern(df).index)


# ── valuing a structure ───────────────────────────────────────────

def test_a_debit_spread_is_long_minus_short():
    h = _FakeHistory({_tk(700): _utc_bars(D, {"14:06": 3.00}),
                      _tk(705): _utc_bars(D, {"14:06": 1.00})})
    s = h.structure_minutes("SPY", D, _legs())
    assert oh.value_at(s, _et("14:06")) == pytest.approx(2.00)


def test_leg_direction_is_case_insensitive():
    """The journal holds both casings; _mark_spread got this wrong once."""
    h = _FakeHistory({_tk(700): _utc_bars(D, {"14:06": 3.00}),
                      _tk(705): _utc_bars(D, {"14:06": 1.00})})
    s = h.structure_minutes("SPY", D, _legs(buy="buy", sell="sell"))
    assert oh.value_at(s, _et("14:06")) == pytest.approx(2.00)


def test_an_unknown_leg_action_is_refused():
    h = _FakeHistory({})
    with pytest.raises(ValueError, match="(?i)action"):
        h.structure_minutes("SPY", D, _legs(buy="OPEN"))


def test_a_missing_leg_makes_the_structure_unpriced_not_half_priced():
    """A spread priced from one leg reports a value that never existed."""
    h = _FakeHistory({_tk(700): _utc_bars(D, {"14:06": 3.00})})
    s = h.structure_minutes("SPY", D, _legs())
    assert oh.value_at(s, _et("14:06")) is None


def test_a_quiet_leg_carries_forward_a_few_minutes_only():
    h = _FakeHistory({_tk(700): _utc_bars(D, {"14:00": 3.00, "14:20": 3.50}),
                      _tk(705): _utc_bars(D, {"14:00": 1.00, "14:20": 1.20})})
    s = h.structure_minutes("SPY", D, _legs())
    assert oh.value_at(s, _et("14:04")) == pytest.approx(2.00)    # carried 4 min
    assert oh.value_at(s, _et("14:12")) is None                   # 12 min stale


def test_value_at_never_reads_the_future():
    h = _FakeHistory({_tk(700): _utc_bars(D, {"14:10": 3.00}),
                      _tk(705): _utc_bars(D, {"14:10": 1.00})})
    s = h.structure_minutes("SPY", D, _legs())
    assert oh.value_at(s, _et("14:06")) is None


def test_extended_hours_bars_are_excluded():
    h = _FakeHistory({_tk(700): _utc_bars(D, {"16:30": 3.00}),
                      _tk(705): _utc_bars(D, {"16:30": 1.00})})
    s = h.structure_minutes("SPY", D, _legs())
    assert s.dropna().empty
