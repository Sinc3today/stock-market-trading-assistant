"""tests/test_gate_scope.py -- each validator belongs to exactly one gate.

VALIDATION_AGENDA.md set Gate 0's exit criterion as "zero P1 FAIL" across the
whole audit. But the audit spans all the gates: C1 asks whether the SAMPLE has
matured, D1 whether it covers more than one market state, D2 whether results
beat chance. Those are Gate 1 and Gate 2 questions.

So Gate 0 -- "can we believe our own journal?" -- required, as a precondition
for starting to collect trades, that we had already collected enough trades.
Every engineering defect under it was closed while it read IN PROGRESS.

The fix is to scope each validator to the gate whose question it answers, in
code rather than prose, so the criterion cannot drift from the validator set
again. A new validator with no gate fails here.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests import forward_audit as fa


def test_every_validator_declares_a_gate():
    missing = [r["id"] for r in fa.run_all() if r.get("gate") is None]
    assert missing == [], (
        f"validator(s) {missing} declare no gate — add one to GATE_OF so the "
        f"gate criteria cannot silently include or exclude them.")


def test_gate_zero_asks_only_about_the_instrument():
    """Sample size, regime coverage and significance are NOT Gate 0."""
    g0 = {r["id"] for r in fa.run_all() if r.get("gate") == 0}
    assert {"C1", "D1", "D2"} & g0 == set(), (
        "sample-maturity validators are assigned to Gate 0 — that is the "
        "circular criterion this test exists to prevent")
    assert {"A1", "A2", "A3", "B1", "B3"} <= g0


def test_sample_maturity_validators_belong_to_the_later_gates():
    by_id = {r["id"]: r.get("gate") for r in fa.run_all()}
    assert by_id["C1"] == 1          # has any structure earned promotion?
    assert by_id["D1"] == 2          # does the sample cover >1 market state?
    assert by_id["D2"] == 2          # is the result distinguishable from noise?


def test_gate_status_reports_per_gate_not_in_aggregate():
    st = fa.gate_status()
    assert set(st) >= {0, 1, 2}
    for gate, info in st.items():
        assert set(info) >= {"p1_fail", "fail", "warn", "pass", "clean"}


def test_a_gate_is_clean_only_when_it_has_no_p1_failure_of_its_own():
    st = fa.gate_status()
    for gate, info in st.items():
        assert info["clean"] == (info["p1_fail"] == 0)


# ── a check that did not run is not a pass ───────────────────────

def test_a_crashing_validator_keeps_its_id_and_fails_loudly():
    """A crash was reported as a WARN under the FUNCTION name, so the check
    dropped out of GATE_OF and its gate could read 'clean' with a dead
    validator inside it. D1 did exactly this on an empty journal."""
    def v_z9_explodes(trades):
        raise RuntimeError("boom")

    orig = fa.VALIDATORS
    try:
        fa.VALIDATORS = [v_z9_explodes]
        r = next(r for r in fa.run_all([]) if r["id"] == "Z9")
    finally:
        fa.VALIDATORS = orig
    assert r["verdict"] == "FAIL"
    assert r["severity"] == "P1"
    assert "CRASHED" in r["headline"]


def test_the_audit_survives_a_completely_empty_journal():
    """A fresh deployment, or any test under an isolated LOG_DIR. D1 raised
    IndexError here and the blanket except hid it."""
    crashed = [r["id"] for r in fa.run_all([]) if "CRASHED" in r["headline"]]
    assert crashed == [], f"validator(s) crashed on an empty journal: {crashed}"
