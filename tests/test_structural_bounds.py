"""tests/test_structural_bounds.py -- B3, the invariant nothing was checking.

A defined-risk position cannot book more than max_profit or lose more than
max_loss. That is arithmetic, not opinion: it needs no live price and no
credit-vs-debit convention. It is the cheapest possible check on a marking
engine, and until now nothing ran it — which is how an open disciplined condor
came to carry a mark implying a -$978 loss on a structure whose floor is -$320.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.forward_audit import v_b3_structural_bounds


def _closed(pnl, max_loss=320.0, max_profit=180.0, **kw):
    """A scored closed condor booking `pnl` dollars."""
    t = {"trade_id": "T1", "strategy": "iron_condor", "book": "disciplined",
         "entry_price": 1.80, "size": 1,
         "outcome": "win" if pnl >= 0 else "loss",
         "max_loss": max_loss, "max_profit": max_profit,
         "pnl_dollars": pnl,
         "exit_price": round(1.80 - pnl / 100.0, 2)}
    t.update(kw)
    return t


def test_a_result_inside_the_envelope_passes():
    r = v_b3_structural_bounds([_closed(120.0)])
    assert r["verdict"] == "PASS"


def test_a_loss_deeper_than_max_loss_fails():
    """The averted bug: -$978 booked on a structure that can only lose $320."""
    r = v_b3_structural_bounds([_closed(-978.0)])
    assert r["verdict"] == "FAIL"
    # The booked figure is net of commissions, so don't assert the gross
    # number back — assert the record is named and the envelope is quoted.
    ev = " ".join(r["evidence"])
    assert "T1" in ev and "-326" in ev


def test_a_win_larger_than_max_profit_fails():
    r = v_b3_structural_bounds([_closed(900.0)])
    assert r["verdict"] == "FAIL"


def test_the_envelope_scales_with_size():
    """A 3-lot may legitimately lose 3x what a 1-lot can."""
    assert v_b3_structural_bounds([_closed(-900.0, size=3)])["verdict"] == "PASS"
    assert v_b3_structural_bounds([_closed(-900.0, size=1)])["verdict"] == "FAIL"


def test_boundary_results_are_not_flagged():
    """Landing exactly on max profit or max loss is a normal outcome."""
    assert v_b3_structural_bounds([_closed(180.0)])["verdict"] == "PASS"
    assert v_b3_structural_bounds([_closed(-320.0)])["verdict"] == "PASS"


def test_open_positions_are_not_judged():
    """Open marks move; only booked results are held to the envelope."""
    t = _closed(-978.0)
    t["outcome"] = "open"
    t.pop("exit_price")
    assert v_b3_structural_bounds([t])["verdict"] != "FAIL"


def test_a_quarantined_breach_does_not_fail_the_audit():
    """integrity() already excludes unscored rows from every headline. Failing
    on them would make B3's FAIL stop meaning 'something reached the books'."""
    bad = _closed(397.0, max_loss=503.0, max_profit=-3.0)
    bad["notes_exit"] = "stop hit"
    bad["exit_price"] = 0.0          # the $0.00 impossible-fill marker
    r = v_b3_structural_bounds([bad, _closed(120.0)])
    assert r["verdict"] == "PASS"
    assert "quarantined" in r["headline"]


def test_records_without_an_envelope_are_skipped_not_assumed():
    t = _closed(5000.0)
    t.pop("max_loss")
    assert v_b3_structural_bounds([t])["verdict"] == "WARN"
