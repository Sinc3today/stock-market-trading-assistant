"""tests/test_unpriced_play_visible.py -- an estimate must not read as a fill.

OptionsLayer already records whether a play came from a real options chain or
from the theoretical model (`source`). That field was never copied into the
saved plan, so by the time the play reached the dashboard the distinction was
gone: a card showing "IRON CONDOR / max profit $300" looks identical whether
the $300 came from live mids or from a model that has never seen a quote.

On 2026-09-08 the chain refused (unpriced leg) and the published play carried
a modelled "~$3.00 total credit". The prior day's real structure priced at
$1.84. Mirroring the modelled version onto a broker means entering a position
whose credit assumption is off by 60%.

This is the same defect family as the paper_broker $1.00 placeholder from the
2026-09-07 audit -- a number the system invented, rendered next to numbers it
measured, with nothing to tell them apart.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.spy_daily_strategy import SPYDailyStrategy
from alerts import web_app


class _RR:
    regime = type("R", (), {"value": "choppy_low_vol"})()
    play = "IRON CONDOR"
    confidence = 0.85
    metrics = {}
    reasons = ["test"]


def _opts(source):
    return {"strategy": "iron_condor", "legs": [], "max_profit": 300.0,
            "max_loss": 350.0, "rr_ratio": "0.86:1", "recommended_dte": 45,
            "exit_rule": "Close at 50%", "source": source}


def test_the_plan_records_whether_the_play_was_actually_priced():
    plan = SPYDailyStrategy._format_plan(date(2026, 9, 8), _RR(),
                                         _opts("theoretical"))
    assert plan["source"] == "theoretical"


def test_a_real_chain_play_is_recorded_as_such():
    plan = SPYDailyStrategy._format_plan(date(2026, 9, 8), _RR(),
                                         _opts("polygon_chain"))
    assert plan["source"] == "polygon_chain"


# ── the card ─────────────────────────────────────────────────────

def _card(source, **kw):
    plan = {"regime": "choppy_low_vol", "strategy": "iron_condor",
            "play": "IRON CONDOR", "ticker": "SPY", "confidence": 0.85,
            "max_profit": 300.0, "max_loss": 350.0, "source": source,
            "legs": [{"action": "SELL", "option_type": "PUT", "strike": 739,
                      "expiration": "2026-10-23",
                      "expiration_estimated": source == "theoretical"}]}
    plan.update(kw)
    return web_app._render_todays_play_card(plan)


def test_an_unpriced_play_says_so_on_the_card():
    html = _card("theoretical")
    assert "not priced" in html.lower() or "unpriced" in html.lower()


def test_an_unpriced_play_marks_its_max_profit_as_an_estimate():
    """$300 from a model and $300 from live mids must not render the same."""
    html = _card("theoretical")
    assert "estimate" in html.lower()


def test_a_priced_play_carries_no_warning():
    html = _card("polygon_chain")
    assert "not priced" not in html.lower()
    assert "unpriced" not in html.lower()


def test_the_expiry_date_shows_even_when_the_play_is_unpriced():
    """The reported bug: today's play showed no DTE date at all."""
    assert "Oct 23" in _card("theoretical") or "10-23" in _card("theoretical")


def test_an_estimated_expiry_is_marked_as_a_target_on_the_card():
    assert "target" in _card("theoretical").lower()
