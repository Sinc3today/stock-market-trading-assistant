"""tests/test_scorecard_page.py -- the /scorecard one-glance forward-test view.

The page's whole job is to make a dishonest number impossible to display, so
the tests focus there: the integrity warning must appear whenever the sample
is dirty, and excluded records must never be folded into a headline.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts.web_app import app, _render_scorecard

client = TestClient(app)


def _card(**over):
    base = {
        "books": {"disciplined": {"n": 10, "wins": 7, "win_pct": 70.0, "total": 800.0,
                                  "avg": 80.0, "worst": -136.0, "excluded": 0}},
        "headline": {"n": 10, "wins": 7, "win_pct": 70.0, "total": 800.0,
                     "avg": 80.0, "worst": -136.0, "excluded": 0},
        "live": {"n": 0, "wins": 0, "win_pct": 0.0, "total": 0.0, "avg": 0.0,
                 "worst": 0.0, "excluded": 0},
        "strategies": [{"book": "disciplined", "strategy": "iron_condor", "n": 10,
                        "wins": 7, "win_pct": 70.0, "total": 800.0, "avg": 80.0}],
        "integrity": {"scored": 10, "unscored": 0, "suspect_fill": 0, "void": 0,
                      "open": 2, "closed": 10, "trust_pct": 100.0,
                      "unscored_recoverable": 0, "unscored_hidden_pnl": 0.0},
        "promotion": [{"bucket": "7DTE", "label": "7DTE condor", "closed": 8, "open": 2,
                       "win_pct": 62.5, "avg": -54.0, "total": -432.0, "target_n": 15,
                       "target_win": 70.0, "target_avg": 20.0, "pct_of_bar": 53.3,
                       "met": False}],
        "open_positions": [{"trade_id": "A1", "ticker": "SPY", "strategy": "iron_condor",
                            "book": "candidate", "bucket": "7DTE",
                            "entry_date": "2026-09-03 09:45 AM EST",
                            "entry_price": 1.6, "size": 1, "max_loss": 340}],
        "predictions": {"sample": 44, "accuracy": 63.6, "correct": 28, "wrong": 16,
                        "skips": 20, "skip_right_pct": 50.0},
        "total_records": 12,
    }
    base.update(over)
    return base


def test_route_returns_200():
    assert client.get("/scorecard").status_code == 200


def test_page_is_in_the_nav():
    assert 'href="/scorecard"' in client.get("/scorecard").text


def test_clean_sample_shows_no_integrity_warning():
    html = _render_scorecard(_card())
    assert "Sample integrity warning" not in html


def test_dirty_sample_raises_the_warning():
    integ = {"scored": 45, "unscored": 32, "suspect_fill": 0, "void": 6,
             "open": 26, "closed": 83, "trust_pct": 54.2,
             "unscored_recoverable": 32, "unscored_hidden_pnl": -718.0}
    html = _render_scorecard(_card(integrity=integ))
    assert "Sample integrity warning" in html
    assert "put_debit_spread" in html
    assert "718" in html          # the hidden P&L is stated, not buried


def test_headline_reports_scored_count_not_total_closed():
    """13 scored of 25 closed must show 13 — the bug this page exists to expose."""
    head = {"n": 13, "wins": 8, "win_pct": 61.5, "total": 837.0, "avg": 64.4,
            "worst": -136.0, "excluded": 12}
    html = _render_scorecard(_card(headline=head))
    assert "13 closed trades with trustworthy" in html


def test_excluded_count_is_rendered_per_book():
    books = {"learning": {"n": 7, "wins": 6, "win_pct": 85.7, "total": 564.0,
                          "avg": 80.6, "worst": -35.0, "excluded": 22}}
    html = _render_scorecard(_card(books=books))
    assert "sc-excl" in html and "22" in html


def test_promotion_bar_shows_progress_and_target():
    html = _render_scorecard(_card())
    assert "8/15 closed" in html
    assert "sc-prom-track" in html


def test_negative_average_candidate_is_visually_flagged():
    """7DTE wins often but loses on average — must not look healthy."""
    assert "is-neg" in _render_scorecard(_card())


def test_open_positions_are_excluded_from_totals_note():
    html = _render_scorecard(_card())
    assert "excluded from every" in html


def test_headline_states_when_a_win_rate_cannot_beat_chance():
    """Audit D2: a bare '61.5%' implies confidence n=13 cannot support."""
    head = {"n": 13, "wins": 8, "win_pct": 61.5, "total": 837.0, "avg": 64.4,
            "worst": -136.0, "excluded": 12, "ci_low": 35.5, "ci_high": 82.3,
            "beats_chance": False}
    html = _render_scorecard(_card(headline=head))
    assert "not distinguishable from a coin flip" in html
    assert "95% CI [36%" in html or "95% CI [35%" in html


def test_headline_credits_a_sample_that_does_beat_chance():
    head = {"n": 40, "wins": 33, "win_pct": 82.5, "total": 3000.0, "avg": 75.0,
            "worst": -200.0, "excluded": 0, "ci_low": 68.1, "ci_high": 91.0,
            "beats_chance": True}
    html = _render_scorecard(_card(headline=head))
    assert "beats chance" in html
    assert "coin flip" not in html


def test_untested_regimes_are_named_as_untested():
    cov = [{"regime": "choppy_low_vol", "n": 19, "state": "covered"},
           {"regime": "trending_high_vol", "n": 0, "state": "untested"},
           {"regime": "choppy_high_vol", "n": 0, "state": "untested"}]
    html = _render_scorecard(_card(regime_coverage=cov))
    assert "2 regimes have never been traded live" in html
    assert "untested" in html


def test_unmarked_open_tail_says_unknown_not_zero():
    expo = {"count": 26, "marked": 0, "unrealized": None, "by_book": {},
            "note": "Could not mark the open tail"}
    html = _render_scorecard(_card(open_exposure=expo))
    assert "unrealized P&amp;L unknown" in html


def test_marked_open_tail_shows_the_number():
    expo = {"count": 26, "marked": 24, "unrealized": 533.0,
            "by_book": {"candidate": 312.0}, "note": "Modelled at SPY 770"}
    html = _render_scorecard(_card(open_exposure=expo))
    assert "533" in html and "unrealized" in html


def test_empty_state_does_not_crash():
    empty = {"books": {}, "headline": {}, "live": {}, "strategies": [],
             "integrity": {}, "promotion": [], "open_positions": [],
             "predictions": {}, "total_records": 0}
    html = _render_scorecard(empty)
    assert "No closed trades yet" in html


def test_theme_tokens_only_no_hardcoded_hex_in_page_css():
    """Design rule: the page must theme correctly in light and dark."""
    from alerts.web_app import _SCORECARD_CSS
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", _SCORECARD_CSS)


def test_books_table_shows_gross_fees_and_net():
    books = {"disciplined": {"n": 21, "wins": 13, "win_pct": 61.9, "total": 1129.0,
                             "avg": 53.8, "worst": -136.0, "excluded": 4,
                             "ci_low": 41.0, "ci_high": 79.0, "beats_chance": False,
                             "net_total": 1048.4, "net_avg": 49.9, "fees": 80.6}}
    html = _render_scorecard(_card(books=books))
    assert "1,129" in html and "1,048" in html      # gross and net both shown
    assert "&minus;$81" in html                      # fees called out
    assert "<th>Net</th>" in html


def test_promotion_note_states_that_the_bar_is_net_of_fees():
    assert "net of commissions" in _render_scorecard(_card())


def test_wide_table_scrolls_inside_its_card():
    """Design rule: the page body must never scroll horizontally."""
    assert "sc-scroll" in _render_scorecard(_card())
