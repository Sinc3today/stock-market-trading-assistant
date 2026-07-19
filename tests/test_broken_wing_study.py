"""tests/test_broken_wing_study.py -- broken-wing butterfly builder invariants.

Guards the defined-risk contract: a BWB must have a CAPPED max loss (it stays
long a far wing), the right leg structure (+1/-2/+1 puts, wide lower wing), and
a haircut that only ever moves the entry premium against us.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.broken_wing_study import (
    BWB_LOWER_WING, BWB_UPPER_WING, build_bwb_legs,
)


def test_bwb_leg_structure():
    built = build_bwb_legs(628.0, 0.15, 45)
    assert built is not None
    legs, net_debit, max_profit, max_loss = built
    # +1 put K_hi, -2 put K_mid, +1 put K_lo
    assert [q for _, _, q in legs] == [1, -2, 1]
    assert all(opt == "put" for opt, _, _ in legs)
    k_hi, k_mid, k_lo = (k for _, k, _ in legs)
    assert k_hi - k_mid == BWB_UPPER_WING          # narrow upper wing
    assert k_mid - k_lo == BWB_LOWER_WING          # wide lower wing (the break)
    assert k_lo < k_mid < k_hi


def test_bwb_max_loss_is_capped_and_defined():
    built = build_bwb_legs(628.0, 0.15, 45)
    legs, net_debit, max_profit, max_loss = built
    # Max loss is the wing differential net of entry premium — always finite,
    # never unlimited (that's the whole point vs a naked ratio spread).
    expected = (BWB_LOWER_WING - BWB_UPPER_WING + net_debit) * 100
    assert abs(max_loss - expected) < 1e-6
    assert max_loss < (BWB_LOWER_WING - BWB_UPPER_WING) * 100 + 100  # bounded


def test_bwb_haircut_worsens_entry_premium():
    base = build_bwb_legs(628.0, 0.15, 45, hurt=0.0)
    hurt = build_bwb_legs(628.0, 0.15, 45, hurt=0.10)
    # A worse fill can only push net_debit UP (collect less credit / pay more).
    assert hurt[1] >= base[1]
    # ...and therefore never increases our structural max profit.
    assert hurt[2] <= base[2]


def test_bwb_rejects_degenerate_low_strike():
    # Tiny spot would push K_lo <= 0 -> no structure.
    assert build_bwb_legs(5.0, 0.15, 45) is None
