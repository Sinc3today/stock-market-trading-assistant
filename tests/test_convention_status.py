"""tests/test_convention_status.py -- "unclassifiable" is not "unknown".

_pnl_convention returns None for two very different situations, and the log
treated them identically:

  * "iron_condur" -- a typo or a new producer nobody wired up. A real bug.
    The right response is loud, and the fix is to add it to a set.
  * "custom" -- what rh_sync emits for a Robinhood position it cannot name
    (3-leg, 5+-leg, or hand-edited). Credit-vs-debit is a property of the
    legs and prices, not of the word "custom". There is no set to add it to.

Both logged at ERROR with "Add it to _CREDIT_STRATEGIES / _DEBIT_STRATEGIES",
which is actionable advice for the first and actively wrong for the second.
Two records fire it on every audit run, so the one message that should mean
"something is broken" has been crying wolf since July.

Refusing to score stays the same in both cases. Only the diagnosis changes.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal import trade_recorder as tr


@pytest.mark.parametrize("name", ["iron_condor", "credit_spread", "broken_wing",
                                  "put_credit_spread"])
def test_known_credit_structures(name):
    assert tr.convention_status(name) == "credit"


@pytest.mark.parametrize("name", ["debit_spread", "single_leg", "butterfly",
                                  "bull_debit"])
def test_known_debit_structures(name):
    assert tr.convention_status(name) == "debit"


@pytest.mark.parametrize("name", ["custom", "none", "CUSTOM", " custom "])
def test_names_that_cannot_be_classified_by_name(name):
    """These are expected and permanent, not a gap waiting to be filled."""
    assert tr.convention_status(name) == "unclassifiable"


@pytest.mark.parametrize("name", ["iron_condur", "jade_lizard", "", None])
def test_a_genuinely_unrecognised_name_is_unknown(name):
    assert tr.convention_status(name) == "unknown"


def test_unclassifiable_still_refuses_to_score():
    """The whole point of the distinction is the diagnosis, not the outcome.
    A 'custom' position must stay unscored exactly as before."""
    assert tr._pnl_convention("custom") is None
    assert tr._pnl_convention("none") is None


def test_every_strategy_in_the_live_journal_is_accounted_for():
    """ENUMERATION: a name in the journal is either classified, or explicitly
    known-unclassifiable. A NEW unrecognised name fails here rather than
    quietly joining the two that already log every run.
    """
    import json
    path = os.path.join(os.path.dirname(__file__), "..", "logs", "trades.json")
    if not os.path.exists(path):
        pytest.skip("no live journal in this environment")
    with open(path) as fh:
        names = {t.get("strategy") for t in json.load(fh)}
    unknown = sorted(n for n in names if tr.convention_status(n) == "unknown")
    assert unknown == [], (
        f"journal holds unclassified strategy name(s) {unknown}. Either add "
        f"to _CREDIT_STRATEGIES/_DEBIT_STRATEGIES, or to "
        f"_UNCLASSIFIABLE_STRATEGIES if the name genuinely cannot determine "
        f"the convention.")
