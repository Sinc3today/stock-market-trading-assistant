"""tests/test_copilot_candidates.py -- QQQ + paper candidates on the copilot.

Candidates are shown with their legs so the reasoning is followable, but a
paper candidate must NEVER read as an instruction to place a trade: one has
already been mistaken for a real position (2026-08). Every row therefore
carries a paper badge and its distance from the promotion bar.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts.web_app import app, _render_candidates_card

client = TestClient(app)


def _cand(**kw):
    base = {
        "trade_id": "Q1", "ticker": "QQQ", "strategy": "iron_condor",
        "book": "candidate", "dte_bucket": "qqq_condor", "outcome": "open",
        "entry_price": 1.87, "size": 1, "max_profit": 187.0, "max_loss": 313.0,
        "entry_date": "2026-09-03 09:45 AM EST",
        "legs": [
            {"action": "SELL", "option_type": "call", "strike": 740,
             "expiry": "2026-10-16"},
            {"action": "BUY", "option_type": "call", "strike": 745,
             "expiry": "2026-10-16"},
            {"action": "SELL", "option_type": "put", "strike": 690,
             "expiry": "2026-10-16"},
            {"action": "BUY", "option_type": "put", "strike": 685,
             "expiry": "2026-10-16"},
        ],
    }
    base.update(kw)
    return base


_BAR = [{"bucket": "qqq_condor", "label": "QQQ condor", "closed": 6, "open": 4,
         "win_pct": 100.0, "avg": 109.0, "total": 654.0, "target_n": 15,
         "target_win": 70.0, "target_avg": 20.0, "pct_of_bar": 40.0,
         "met": False}]


def test_candidate_legs_are_rendered():
    html = _render_candidates_card([_cand()], _BAR)
    assert "740" in html and "690" in html
    assert "legs" in html


def test_candidate_shows_the_ticker():
    assert "QQQ" in _render_candidates_card([_cand()], _BAR)


def test_candidate_is_badged_as_paper():
    """The whole point: legs visible, but never mistakable for a live play."""
    html = _render_candidates_card([_cand()], _BAR)
    assert "paper" in html.lower()


def test_candidate_shows_distance_from_the_promotion_bar():
    assert "6/15 to bar" in _render_candidates_card([_cand()], _BAR)


def test_candidate_card_states_it_is_not_an_instruction():
    html = _render_candidates_card([_cand()], _BAR)
    assert "not</b> as an instruction" in html


def test_candidate_shows_credit_and_risk():
    html = _render_candidates_card([_cand()], _BAR)
    assert "credit $1.87" in html
    assert "max loss $313" in html


def test_no_candidates_shows_a_calm_empty_state():
    html = _render_candidates_card([], _BAR)
    assert "No candidate opened today" in html
    assert "nothing to do" in html


def test_candidate_without_a_known_bar_still_renders():
    html = _render_candidates_card([_cand(dte_bucket="brand_new")], _BAR)
    assert "QQQ" in html and "candidate" in html


def test_multiple_candidates_all_appear():
    html = _render_candidates_card(
        [_cand(trade_id="A"), _cand(trade_id="B", ticker="SPY",
                                    dte_bucket="7DTE")], _BAR)
    assert html.count("cand-row") == 2
    assert "SPY" in html and "QQQ" in html


# ── page + endpoint wiring ───────────────────────────────────────

def test_copilot_page_renders_with_qqq():
    r = client.get("/copilot")
    assert r.status_code == 200
    assert 'id="qqq-price"' in r.text
    assert "Paper candidates" in r.text


def test_spot_endpoint_returns_both_tickers():
    r = client.get("/copilot/spot")
    assert r.status_code == 200
    body = r.json()
    assert "price" in body and "qqq" in body


def test_ticker_spot_is_generic():
    from alerts.web_app import _ticker_spot, _spy_spot, _qqq_spot
    assert callable(_ticker_spot)
    assert callable(_spy_spot) and callable(_qqq_spot)
