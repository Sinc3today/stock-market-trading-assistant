"""tests/test_duplicate_detection.py -- C4 must flag real duplicates only.

C4 asks "is n inflated by the same trade recorded twice?". Its signature was
(entry_date, ticker, strategy, entry_price, size) -- which omits the two
fields that actually distinguish two SPY iron condors opened in the same
minute: the expiration and the DTE bucket.

The live journal's only "duplicate" is exactly that false positive:

    F1A205B7  0DTE     legs expire 2026-05-27
    200A9567  1-3DTE   legs expire 2026-05-29

Two different structures, opened together, colliding because the signature
could not see the difference -- and because entry_price on both is 1.0, the
$1.00 placeholder, which erased the last field that might have separated them.

Both are also already VOID ("pre-Phase-4b synthetic stub"), so neither can
inflate anything. A validator that fires on records excluded from every
headline trains you to ignore it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.forward_audit import v_c4_duplicates


def _t(tid, *, expiry="2026-06-19", bucket="45DTE", outcome="win", **kw):
    t = {"trade_id": tid, "ticker": "SPY", "strategy": "iron_condor",
         "entry_price": 1.80, "size": 1, "entry_date": "2026-05-27 09:30 AM EST",
         "dte_bucket": bucket, "outcome": outcome, "exit_price": 0.9,
         "pnl_dollars": 90.0,
         "legs": [{"action": "SELL", "option_type": "PUT", "strike": 739,
                   "expiry": expiry}]}
    t.update(kw)
    return t


def test_a_genuine_duplicate_is_still_caught():
    r = v_c4_duplicates([_t("AAA"), _t("BBB")])
    assert r["verdict"] == "WARN"


def test_same_minute_different_expiration_is_not_a_duplicate():
    """The live false positive: 0DTE and 1-3DTE opened in the same minute."""
    a = _t("F1A205B7", expiry="2026-05-27", bucket="0DTE")
    b = _t("200A9567", expiry="2026-05-29", bucket="1-3DTE")
    r = v_c4_duplicates([a, b])
    assert r["verdict"] == "PASS", r["headline"]


def test_same_minute_different_dte_bucket_is_not_a_duplicate():
    a = _t("AAA", bucket="7DTE")
    b = _t("BBB", bucket="45DTE")
    assert v_c4_duplicates([a, b])["verdict"] == "PASS"


def test_quarantined_records_cannot_inflate_n_so_are_not_flagged():
    """Both live 'duplicates' are void. A void record is excluded from every
    headline, so it is not capable of the harm C4 exists to detect."""
    a = _t("AAA", outcome="void")
    b = _t("BBB", outcome="void")
    assert v_c4_duplicates([a, b])["verdict"] == "PASS"


def test_a_duplicate_among_scored_records_still_warns():
    """Skipping the void ones must not become skipping the real ones."""
    a = _t("AAA")
    b = _t("BBB")
    c = _t("CCC", outcome="void")
    r = v_c4_duplicates([a, b, c])
    assert r["verdict"] == "WARN"
    assert "AAA" in " ".join(r["evidence"]) or "BBB" in " ".join(r["evidence"])


def test_the_live_journal_has_no_real_duplicates():
    import json
    path = os.path.join(os.path.dirname(__file__), "..", "logs", "trades.json")
    if not os.path.exists(path):
        import pytest
        pytest.skip("no live journal in this environment")
    with open(path) as fh:
        r = v_c4_duplicates(json.load(fh))
    assert r["verdict"] == "PASS", r["headline"] + " " + str(r.get("evidence"))
