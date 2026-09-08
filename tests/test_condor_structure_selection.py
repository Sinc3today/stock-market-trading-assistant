"""tests/test_condor_structure_selection.py -- picking the legs of a condor.

Two defects found while chasing "today's play has no expiry date" on
2026-09-08.

1. SELECTION IGNORED A CONSTRAINT IT THEN ENFORCED.
   find_iron_condor picks the short strike by delta, then takes the wing at
   `short + wing_width` with no regard for whether that contract has a usable
   price -- and then refuses the ENTIRE structure if any leg is unpriced. On
   2026-09-08, 49 of 60 calls were priceable, but the blind wing pick landed
   on K=808 (one of the 11 that were not) and the whole trading day fell back
   to a theoretical, unpriced play. Choosing among contracts that HAVE a price
   is not the same as inventing one.

2. max_loss WAS COMPUTED FROM THE REQUESTED WIDTH, NOT THE ACTUAL STRIKES.
   `max_loss = (wing_width - net_credit) * 100` uses the parameter, so any
   time the grid does not have a strike exactly `wing_width` away, the
   reported risk is wrong. SPY's grid is 1-wide near the money and 5-wide far
   out, so this understates max loss precisely where the wings live. A condor
   whose sides end up different widths has only one honest max loss: the
   wider side.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.options_chain import OptionsChain


def _c(strike, delta, mark, otype="call"):
    return {"strike": float(strike), "delta": delta, "mark": mark, "mid": None,
            "type": otype, "expiration": "2026-10-23", "dte": 45,
            "open_interest": 100}


class _Chain(OptionsChain):
    """Real selection logic, canned contracts."""
    def __init__(self, calls, puts):
        self._calls, self._puts = calls, puts

    def get_chain(self, ticker, otype, min_exp, max_exp, **kw):
        return self._calls if otype == "call" else self._puts


def _calls_with_unpriced_wing():
    # short lands at 803 (delta .16); the 808 wing has no price, 809 does.
    return [_c(803, 0.16, 2.68), _c(808, 0.12, None), _c(809, 0.11, 1.90)]


def _puts_ok():
    return [_c(727, -0.16, 4.02, "put"), _c(722, -0.14, 3.62, "put")]


def test_an_unpriced_wing_does_not_kill_the_whole_structure():
    """The reported symptom: one unpriced wing sent the entire day to a
    theoretical play, while 49 of 60 calls had prices."""
    ic = _Chain(_calls_with_unpriced_wing(), _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45)
    assert ic is not None


def test_the_substituted_wing_is_the_next_priceable_strike():
    ic = _Chain(_calls_with_unpriced_wing(), _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45)
    assert ic["long_call"]["strike"] == 809.0


def test_a_structure_with_no_priceable_wing_at_all_is_still_refused():
    """Widening the search must not become 'accept anything'."""
    calls = [_c(803, 0.16, 2.68), _c(808, 0.12, None)]
    assert _Chain(calls, _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45) is None


def test_max_loss_uses_the_actual_strike_distance():
    """With the wing at 809 instead of 808 the call side is 6 wide, not 5."""
    ic = _Chain(_calls_with_unpriced_wing(), _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45, wing_width=5.0)
    width = ic["long_call"]["strike"] - ic["short_call"]["strike"]
    expected = (width - ic["net_credit"]) * 100
    assert ic["max_loss"] == pytest.approx(expected, abs=0.02)


def test_max_loss_takes_the_wider_side_when_the_sides_differ():
    """A condor can only lose on one side, so its risk is the WIDER wing."""
    ic = _Chain(_calls_with_unpriced_wing(), _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45, wing_width=5.0)
    call_w = ic["long_call"]["strike"] - ic["short_call"]["strike"]
    put_w = ic["short_put"]["strike"] - ic["long_put"]["strike"]
    assert call_w != put_w, "fixture should exercise asymmetric wings"
    assert ic["max_loss"] == pytest.approx(
        (max(call_w, put_w) - ic["net_credit"]) * 100, abs=0.02)


def test_the_actual_widths_are_reported():
    """Downstream cannot re-derive risk it was never told about."""
    ic = _Chain(_calls_with_unpriced_wing(), _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45)
    assert ic["call_width"] == 6.0
    assert ic["put_width"] == 5.0


def test_an_exactly_priced_grid_is_unchanged():
    """No behaviour change on the normal path."""
    calls = [_c(803, 0.16, 2.68), _c(808, 0.12, 1.95)]
    ic = _Chain(calls, _puts_ok()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45, wing_width=5.0)
    assert ic["long_call"]["strike"] == 808.0
    assert ic["max_loss"] == pytest.approx((5.0 - ic["net_credit"]) * 100, abs=0.02)


# ── all four legs must share one expiration ──────────────────────
#
# find_iron_condor fetches a RANGE of expirations (dte_target +/- tolerance)
# and then picks each leg by delta or strike-distance, with nothing anchoring
# the four picks to the same expiry. On 2026-09-08 it returned structures
# spanning THREE expirations for both SPY and QQQ:
#
#     short_call 2026-10-23 / long_call 2026-10-23
#     short_put  2026-10-30 / long_put  2026-10-16
#
# That is not an iron condor. Its reported "max loss $96" is fiction -- once
# the long put expires a week before the short put, the downside is open. The
# returned `expiration` field was the short call's, so every downstream
# consumer (paper_broker, expiry_resolver, exit_manager) believed it was a
# single-expiry structure.
#
# It is rare (1 of 113 journal records) because it needs the delta match to
# straddle expiries. Filtering the pool to priceable contracts changed which
# contracts win that match and made a latent bug active -- a reminder that
# narrowing a candidate set is a behaviour change, not just a filter.


def _multi_expiry_calls():
    a = [_c(803, 0.16, 2.68), _c(808, 0.12, 1.95)]
    b = [_c(803, 0.17, 3.90), _c(808, 0.13, 3.10)]
    for c in b:
        c["expiration"] = "2026-10-30"
    return a + b


def _multi_expiry_puts():
    a = [_c(727, -0.16, 4.02, "put"), _c(722, -0.14, 3.62, "put")]
    b = [_c(727, -0.15, 6.50, "put"), _c(722, -0.13, 6.10, "put")]
    for c in b:
        c["expiration"] = "2026-10-30"
    return a + b


def test_all_four_legs_share_one_expiration():
    ic = _Chain(_multi_expiry_calls(), _multi_expiry_puts()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45)
    assert ic is not None
    exps = {ic[k]["expiration"] for k in
            ("short_call", "long_call", "short_put", "long_put")}
    assert len(exps) == 1, f"condor spans {len(exps)} expirations: {exps}"


def test_the_reported_expiration_is_the_one_every_leg_uses():
    """The field was taken from the short call alone, so a mixed structure
    still announced a single clean expiry downstream."""
    ic = _Chain(_multi_expiry_calls(), _multi_expiry_puts()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45)
    for k in ("short_call", "long_call", "short_put", "long_put"):
        assert ic[k]["expiration"] == ic["expiration"]


def test_an_expiry_that_cannot_form_a_full_condor_is_not_used():
    """One expiry has every leg; the other is missing a call wing. Picking the
    incomplete one and borrowing the wing is what produced the bug."""
    calls = [_c(803, 0.16, 2.68), _c(808, 0.12, 1.95)]
    partial = [_c(803, 0.161, 3.90)]           # closer delta, no wing
    for c in partial:
        c["expiration"] = "2026-10-30"
    ic = _Chain(calls + partial, _multi_expiry_puts()).find_iron_condor(
        ticker="SPY", spot=769.48, dte_target=45)
    assert ic is not None
    assert ic["expiration"] == "2026-10-23"
