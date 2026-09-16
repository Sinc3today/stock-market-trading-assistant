"""tests/test_intraday_quarantine.py -- records that cannot measure anything.

Every intraday record entered before 2026-09-16 was priced by a pricer that
read each leg's last TRADE, which on this data plan is a median 20 minutes
stale. Recorded entries sit a median 32% from the market (worst 175%), and
until 2026-09-13 every same-day trade was also closed five minutes after entry
by a time stop that was true from the first check.

Those records are not wrong in a way that can be corrected in place: a replay
is a reconstruction, not a record of what happened. So they are classified
UNMEASURED and excluded from every headline, exactly as VOID records are —
counted, visible, never scored.

The cost of this is honest and large: the disciplined book's headline drops
from n=20 / +$1,187 to n=6 / -$13, because that headline was mostly its
intraday sleeve. Reporting a number we cannot stand behind is worse.

learning.intraday_rescore remains the way to ask what those trades did.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import forward_scorecard as fs


def _t(**kw):
    t = {"trade_id": "T1", "book": "learning", "strategy": "put_debit_spread",
         "dte_bucket": "0DTE", "size": 1, "entry_price": 0.68, "exit_price": 1.00,
         "pnl_dollars": 32.0, "outcome": "win",
         "entry_date": "2026-09-14 09:45 AM EST"}
    t.update(kw)
    return t


def test_a_same_day_record_from_before_the_fix_cannot_be_measured():
    assert fs.integrity(_t()) == fs.UNMEASURED


def test_a_one_to_three_day_record_from_before_the_fix_cannot_be_measured():
    assert fs.integrity(_t(dte_bucket="1-3DTE")) == fs.UNMEASURED


def test_a_record_priced_after_the_fix_is_scored_normally():
    assert fs.integrity(_t(entry_date="2026-09-16 09:45 AM EST")) == fs.SCORED


def test_the_swing_book_is_untouched():
    """45DTE prices through options_layer, a different path."""
    assert fs.integrity(_t(dte_bucket="45DTE")) == fs.SCORED


def test_a_void_record_stays_void():
    """Void is a stronger statement and keeps precedence."""
    assert fs.integrity(_t(outcome="void")) == fs.VOID


def test_an_open_position_is_not_closed_and_so_not_classified_here():
    assert fs.is_closed(_t(outcome="open")) is False


def test_the_cutoff_is_a_named_constant():
    assert fs.INTRADAY_PRICE_FIX_DATE == "2026-09-16"


# ── what the books do with them ───────────────────────────────────

def test_headline_stats_exclude_them_and_say_so():
    st = fs.book_stats([_t(), _t(trade_id="T2")])["learning"]
    assert st["n"] == 0
    assert st["excluded"] == 2


def test_they_are_counted_in_the_integrity_summary_not_dropped():
    s = fs.integrity_summary([_t(), _t(trade_id="T2", dte_bucket="45DTE")])
    assert s[fs.UNMEASURED] == 1
    assert s["closed"] == 2


def test_a_scored_record_still_reaches_the_headline():
    st = fs.book_stats([_t(dte_bucket="45DTE")])["learning"]
    assert st["n"] == 1 and st["wins"] == 1


# ── the audit must be able to clear ───────────────────────────────

def test_the_entry_price_check_skips_records_we_already_quarantined():
    """A5 exists to catch NEW drift. If it keeps failing on the 52 records we
    have already set aside, Gate 0 can never clear and the check stops meaning
    'something is wrong now'."""
    from backtests import forward_audit as fa

    class _H:
        def __init__(self): self.calls = 0
        def structure_minutes(self, *a, **k):
            self.calls += 1
            raise AssertionError("quarantined records must not be re-priced")

    t = _t(legs=[{"action": "BUY", "option_type": "put", "strike": 759,
                  "expiration": "2026-09-14"}])
    r = fa.v_a5_entry_price_reality([t], history=_H())
    assert r["verdict"] != "FAIL"


def test_no_pre_fix_intraday_record_in_the_live_journal_is_still_scored():
    path = os.path.join(os.path.dirname(__file__), "..", "logs", "trades.json")
    if not os.path.exists(path):
        pytest.skip("no live journal in this environment")
    with open(path) as fh:
        trades = json.load(fh)
    leaked = [t["trade_id"] for t in trades
              if t.get("dte_bucket") in ("0DTE", "1-3DTE")
              and str(t.get("entry_date", ""))[:10] < fs.INTRADAY_PRICE_FIX_DATE
              and fs.integrity(t) == fs.SCORED]
    assert leaked == [], f"still scored despite quarantine: {leaked}"
