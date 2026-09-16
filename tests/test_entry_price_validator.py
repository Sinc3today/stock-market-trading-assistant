"""tests/test_entry_price_validator.py -- A5: do recorded entries match the market?

Every other integrity check in the audit reads the journal against itself. This
one reads the journal against the MARKET: it re-prices each intraday paper
entry from real per-contract minute bars at the minute it was recorded, and
fails when the two drift apart.

It exists because nothing noticed a 30% median error for months. The exit side
had B1 (impossible $0.00 fills) and B3 (impossible P&L); the entry side had
nothing at all, so a trade could be opened at a price that never traded and
every downstream number inherited it silently.

Deliberately scoped: intraday buckets only (the daily 45DTE play prices from a
different path), the most recent trades only (each check costs two network
fetches), and a structure with no bars is reported unpriced rather than scored.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

import pandas as pd
import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests import forward_audit as fa

ET = pytz.timezone("US/Eastern")


# Fixtures are dated ON OR AFTER 2026-09-16 deliberately. Intraday records from
# before that are quarantined as UNMEASURED (priced by the stale-last-trade
# pricer), and A5 skips them — its job is to catch drift in NEW entries, not to
# keep re-failing on records already set aside. A fixture dated earlier would
# silently never be checked.
def _trade(tid, entry_price, *, bucket="0DTE", strategy="put_debit_spread",
           day="2026-09-16", hhmm="09:45 AM"):
    return {"trade_id": tid, "strategy": strategy, "dte_bucket": bucket,
            "book": "learning", "entry_price": entry_price, "size": 1,
            "outcome": "open", "entry_date": f"{day} {hhmm} EST",
            "legs": [{"action": "BUY", "option_type": "put", "strike": 759,
                      "expiration": day},
                     {"action": "SELL", "option_type": "put", "strike": 756,
                      "expiration": day}]}


class _History:
    """Returns a flat structure value for every minute of the session."""
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def structure_minutes(self, underlying, day, legs, use_cache=True):
        self.calls += 1
        if self.value is None:
            idx = pd.date_range(f"{day} 09:30", f"{day} 16:00", freq="1min",
                                tz="US/Eastern")
            return pd.Series([float("nan")] * len(idx), index=idx)
        idx = pd.date_range(f"{day} 09:30", f"{day} 16:00", freq="1min",
                            tz="US/Eastern")
        return pd.Series([self.value] * len(idx), index=idx)


def _run(trades, real):
    return fa.v_a5_entry_price_reality(trades, history=_History(real))


def test_entries_that_match_the_market_pass():
    r = _run([_trade("T1", 0.86)], real=0.87)
    assert r["verdict"] == "PASS"


def test_a_badly_mispriced_entry_fails():
    """5A6E7351: recorded $0.26 against a real $0.68."""
    r = _run([_trade("T1", 0.26)], real=0.68)
    assert r["verdict"] == "FAIL"
    assert "T1" in " ".join(r["evidence"])


def test_a_persistent_moderate_drift_fails_even_when_no_single_trade_is_wild():
    """The live population: median 30% off, worst 62%. Each one alone looks
    survivable; together they mean the book is priced by something other than
    the market."""
    trades = [_trade(f"T{i}", 0.70) for i in range(6)]
    r = _run(trades, real=1.00)          # every entry 30% low
    assert r["verdict"] == "FAIL"


def test_small_errors_are_tolerated():
    """Last trades and fills never agree exactly; the check must not cry wolf."""
    r = _run([_trade(f"T{i}", 0.98) for i in range(6)], real=1.00)
    assert r["verdict"] == "PASS"


def test_a_credit_structure_is_compared_on_magnitude_not_sign():
    """structure_minutes returns a NEGATIVE value for a sold structure (you
    receive it); the journal stores the credit as a positive number."""
    t = _trade("T1", 0.70, strategy="iron_condor")
    r = fa.v_a5_entry_price_reality([t], history=_History(-0.69))
    assert r["verdict"] == "PASS"


def test_trades_with_no_bars_are_reported_not_scored():
    r = _run([_trade("T1", 0.26)], real=None)
    assert r["verdict"] != "FAIL"
    assert "unpriced" in (r["headline"] + " ".join(r["evidence"])).lower()


def test_the_daily_play_is_not_checked_here():
    """45DTE prices from options_layer, a different path with its own checks."""
    h = _History(1.00)
    fa.v_a5_entry_price_reality([_trade("T1", 0.26, bucket="45DTE")], history=h)
    assert h.calls == 0


def test_only_the_most_recent_trades_are_checked():
    """Each check costs two network fetches; the audit must stay runnable."""
    h = _History(1.00)
    many = [_trade(f"T{i}", 1.00, day="2026-09-16") for i in range(60)]
    fa.v_a5_entry_price_reality(many, history=h)
    assert h.calls <= fa.ENTRY_PRICE_SAMPLE


def test_an_unparseable_entry_time_is_skipped_not_guessed():
    t = _trade("T1", 0.26)
    t["entry_date"] = "sometime tuesday"
    assert _run([t], real=0.68)["verdict"] != "FAIL"


def test_the_validator_belongs_to_gate_zero():
    """It asks whether the instrument is honest, not whether the sample is big."""
    assert fa.GATE_OF["A5"] == 0
