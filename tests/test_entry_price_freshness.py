"""tests/test_entry_price_freshness.py -- price both legs from one moment, or not at all.

Recorded entry prices on intraday paper trades were wrong by a median of 30%
and as much as 62% (5A6E7351: recorded $0.26, real $0.68). The cause is not a
model — LiveChainPricer uses real chain data. It is that this plan's snapshot
carries no bid/ask, so each leg is priced from `day.close`, the last AGGREGATE
print for that contract, and contracts print at different times:

    7A64308A, entered 11:20: both legs' recorded prices match trades from
    10:38-11:03, when SPY was $764.2 against $765.5 at entry -> 30% error.
    CD59E8B4, entered 10:30: legs matched 09:55-10:05, but SPY had barely
    moved, so the same staleness produced a 1% error. Luck, not accuracy.

Subtracting two prices struck at different moments gives a spread value that
never existed — the same defect family as the mixed-expiration condor and the
$1,490 mixed-casing mark.

The snapshot does carry `day.last_updated` per contract, and it tracks
liquidity: on 2026-09-15 the heavily traded SPY strikes updated at 16:30 while
the 790 put (12 contracts traded all day) last updated at 14:45 — an hour and
three quarters stale. So freshness is measurable, and a stale leg is knowable
BEFORE it becomes a recorded entry price.

Rule: a structure prices only when every leg has a known as-of time, no leg is
older than config.MARK_MAX_AGE_MINUTES, and the legs are struck within
config.MARK_MAX_LEG_SKEW_MINUTES of each other. Otherwise it is unpriceable
and nothing opens. Refusing to trade is the cheap failure; booking a fiction
is the expensive one.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from data.options_chain import OptionsChain
from signals.intraday_structure_builder import LiveChainPricer, select_legs

ET = pytz.timezone("US/Eastern")
NOW = ET.localize(datetime(2026, 9, 16, 11, 20))
EXP = "2026-09-16"


# ── what the snapshot hands us ────────────────────────────────────

def _snapshot(strike, cp, *, close=1.20, vwap=None, bid=None, ask=None,
              last_updated=NOW, volume=500):
    day = SimpleNamespace(
        close=close, vwap=vwap, volume=volume, open=None, high=None, low=None,
        last_updated=int(last_updated.timestamp() * 1e9) if last_updated else None)
    quote = SimpleNamespace(bid=bid, ask=ask) if (bid and ask) else None
    return SimpleNamespace(
        details=SimpleNamespace(ticker=f"O:SPY..{cp}{strike}", strike_price=float(strike),
                                expiration_date=EXP, contract_type=cp),
        greeks=SimpleNamespace(delta=0.3, gamma=None, theta=None, vega=None),
        last_quote=quote, day=day, implied_volatility=0.2, open_interest=100)


def test_the_as_of_time_is_kept_and_read_as_eastern():
    c = OptionsChain._normalise(_snapshot(755, "call"))
    assert c["as_of"] is not None
    assert c["as_of"].strftime("%H:%M") == "11:20"


def test_a_snapshot_with_no_timestamp_has_an_unknown_as_of():
    c = OptionsChain._normalise(_snapshot(755, "call", last_updated=None))
    assert c["as_of"] is None


@pytest.mark.parametrize("kw,expected", [
    ({"bid": 1.10, "ask": 1.30}, "quote_mid"),
    ({"close": 1.20}, "day_close"),
    ({"close": None, "vwap": 1.25}, "vwap"),
])
def test_the_price_records_where_it_came_from(kw, expected):
    """A quote midpoint and a two-hour-old print are not the same evidence."""
    c = OptionsChain._normalise(_snapshot(755, "call", **kw))
    assert c["mark_source"] == expected


# ── the pricer's gates ────────────────────────────────────────────

class _Chain:
    def __init__(self, contracts):
        self._c = contracts

    def get_chain(self, ticker, contract_type, min_expiration, max_expiration,
                  strike_min=None, strike_max=None, limit=50):
        return [c for c in self._c if c["type"] == contract_type
                and min_expiration.isoformat() <= c["expiration"] <= max_expiration.isoformat()]


def _c(strike, cp, mark, *, age_min=1, source="day_close"):
    return {"ticker": f"O:SPY..{cp}{strike}", "strike": float(strike), "expiration": EXP,
            "dte": 0, "type": cp, "mid": None, "mark": mark, "bid": None, "ask": None,
            "delta": None, "volume": 500, "mark_source": source,
            "as_of": None if age_min is None else NOW - timedelta(minutes=age_min)}


def _price(contracts, spot=500.0):
    legs = select_legs("iron_condor", spot=spot)
    return LiveChainPricer(_Chain(contracts)).price(
        legs, "iron_condor", "0DTE", spot=spot, as_of=date(2026, 9, 16), now=NOW)


def _condor(**kw):
    return [_c(497, "put", 1.20, **kw), _c(492, "put", 0.40, **kw),
            _c(503, "call", 1.10, **kw), _c(508, "call", 0.35, **kw)]


def test_a_fresh_structure_still_prices():
    out = _price(_condor(age_min=1))
    assert out is not None and round(out["entry_price"], 2) == 1.55


def test_every_leg_carries_its_provenance_into_the_journal():
    out = _price(_condor(age_min=1))
    for leg in out["legs"]:
        assert leg["mark_source"] == "day_close"
        assert leg["as_of"] is not None


def test_the_structure_records_the_moment_it_was_priced():
    out = _price(_condor(age_min=1))
    assert out["price_as_of"] is not None


def test_a_stale_leg_makes_the_structure_unpriceable():
    """The 790 put on 2026-09-15 was 105 minutes stale at the close."""
    legs = _condor(age_min=1)
    legs[0] = _c(497, "put", 1.20, age_min=config.MARK_MAX_AGE_MINUTES + 5)
    assert _price(legs) is None


def test_legs_struck_too_far_apart_make_the_structure_unpriceable():
    """Both legs fresh enough on their own, but 25 minutes apart: their
    difference is a price that never existed at any single moment."""
    legs = _condor(age_min=1)
    legs[0] = _c(497, "put", 1.20, age_min=config.MARK_MAX_LEG_SKEW_MINUTES + 20)
    legs[1] = _c(492, "put", 0.40, age_min=config.MARK_MAX_LEG_SKEW_MINUTES + 21)
    legs[2] = _c(503, "call", 1.10, age_min=1)
    legs[3] = _c(508, "call", 0.35, age_min=1)
    assert _price(legs) is None


def test_an_unknown_as_of_is_refused_rather_than_assumed_fresh():
    legs = _condor(age_min=1)
    legs[0] = _c(497, "put", 1.20, age_min=None)
    assert _price(legs) is None


def test_a_real_quote_is_accepted_even_when_the_day_print_is_old():
    """A quote midpoint IS the current price; its age does not matter the way
    a last-trade's does. This plan has no quotes, but a better one would."""
    legs = [_c(497, "put", 1.20, age_min=90, source="quote_mid"),
            _c(492, "put", 0.40, age_min=90, source="quote_mid"),
            _c(503, "call", 1.10, age_min=90, source="quote_mid"),
            _c(508, "call", 0.35, age_min=90, source="quote_mid")]
    assert _price(legs) is not None


def test_the_thresholds_come_from_config():
    assert config.MARK_MAX_AGE_MINUTES > 0
    assert 0 < config.MARK_MAX_LEG_SKEW_MINUTES <= config.MARK_MAX_AGE_MINUTES
