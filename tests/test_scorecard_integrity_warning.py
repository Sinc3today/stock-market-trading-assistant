"""tests/test_scorecard_integrity_warning.py -- explain the RIGHT dirty sample.

The scorecard's integrity warning fires whenever data trust drops below 90%,
and it explained every such drop with the 2026-09-06 bug: strategy names that
matched no branch in the P&L engine and booked $0.

Since 2026-09-15 the dominant population is different — 50 intraday records
quarantined as UNMEASURED because each leg was priced from a last trade a
median 20 minutes stale. Trust fell to 37%, and the page would have blamed a
bug that was fixed nine days earlier.

A number explained by the wrong cause is worse than an unexplained number: it
sends you to fix the wrong thing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts import web_app
from learning import forward_scorecard as fs


def _card(**integ):
    base = {fs.SCORED: 10, fs.UNSCORED: 0, fs.UNMEASURED: 0, fs.SUSPECT_FILL: 0,
            fs.VOID: 0, "open": 0, "closed": 10, "trust_pct": 100.0,
            "unscored_recoverable": 0, "unscored_hidden_pnl": 0.0}
    base.update(integ)
    return {"integrity": base, "headline": {}, "live": {}, "books": {},
            "strategies": [], "promotion": [], "open_positions": [],
            "open_exposure": {}, "regime_coverage": [], "predictions": {},
            "total_records": base["closed"]}


def test_quarantined_records_are_explained_by_their_own_cause():
    html = web_app._render_scorecard(
        _card(**{fs.SCORED: 6, fs.UNMEASURED: 50, "closed": 56, "trust_pct": 37.1}))
    assert "cannot be measured" in html
    assert "intraday_rescore" in html
    assert "matched no branch" not in html      # the old, wrong explanation


def test_the_old_zero_pnl_cause_is_still_explained_when_it_applies():
    html = web_app._render_scorecard(
        _card(**{fs.SCORED: 6, fs.UNSCORED: 5, "closed": 11, "trust_pct": 54.5,
                 "unscored_hidden_pnl": 315.0}))
    assert "matched no branch" in html
    assert "cannot be measured" not in html


def test_both_causes_appear_when_both_are_present():
    html = web_app._render_scorecard(
        _card(**{fs.SCORED: 6, fs.UNSCORED: 5, fs.UNMEASURED: 50, "closed": 61,
                 "trust_pct": 9.8}))
    assert "cannot be measured" in html and "matched no branch" in html


def test_a_clean_sample_shows_no_warning():
    html = web_app._render_scorecard(_card())
    assert "Sample integrity warning" not in html


def test_the_trust_bar_counts_quarantined_records():
    """50 records missing from the bar while dragging the percentage down is
    how a chart stops adding up."""
    html = web_app._render_scorecard(
        _card(**{fs.SCORED: 6, fs.UNMEASURED: 50, "closed": 56, "trust_pct": 37.1}))
    assert "50 unmeasured" in html
