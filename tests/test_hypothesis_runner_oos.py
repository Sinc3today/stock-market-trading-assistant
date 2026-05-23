"""Phase 1 correctness fix: hypothesis_runner verdict is OOS-based, with
≥30-OOS-trade sample-size floor (else auto-inconclusive)."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.hypothesis_runner import (
    HypothesisRunner, MIN_OOS_TRADES, SHARPE_ACCEPT_DELTA, SHARPE_REJECT_DELTA,
    PNL_REJECT_DELTA,
)


def _bt(trades_is, trades_oos, sharpe_is, sharpe_oos, pnl_is, pnl_oos, win_rate=60.0):
    return {
        "trades":   trades_is + trades_oos,
        "win_rate": win_rate,
        "pnl":      pnl_is + pnl_oos,
        "sharpe":   (sharpe_is + sharpe_oos) / 2,
        "is":  {"trades": trades_is,  "win_rate": win_rate, "pnl": pnl_is,  "sharpe": sharpe_is},
        "oos": {"trades": trades_oos, "win_rate": win_rate, "pnl": pnl_oos, "sharpe": sharpe_oos},
    }


def test_min_oos_trades_constant_is_thirty():
    assert MIN_OOS_TRADES == 30


def test_verdict_accepted_on_oos_improvement_above_thresholds():
    baseline = _bt(trades_is=120, trades_oos=80, sharpe_is=1.0, sharpe_oos=1.0, pnl_is=500, pnl_oos=500)
    modified = _bt(trades_is=120, trades_oos=80, sharpe_is=1.0, sharpe_oos=1.20, pnl_is=500, pnl_oos=700)
    deltas = HypothesisRunner._deltas(baseline, modified)
    assert HypothesisRunner._verdict(deltas, modified) == "accepted"


def test_verdict_rejected_on_oos_regression():
    baseline = _bt(120, 80, 1.0, 1.0, 500, 500)
    modified = _bt(120, 80, 1.0, 0.80, 500, 300)   # OOS sharpe down 0.20
    deltas = HypothesisRunner._deltas(baseline, modified)
    assert HypothesisRunner._verdict(deltas, modified) == "rejected"


def test_verdict_inconclusive_when_oos_trades_below_floor():
    """The classic bug: a HUGE OOS improvement on only 12 OOS trades is noise."""
    baseline = _bt(trades_is=120, trades_oos=12, sharpe_is=1.0, sharpe_oos=1.0, pnl_is=500, pnl_oos=100)
    modified = _bt(trades_is=120, trades_oos=12, sharpe_is=1.0, sharpe_oos=2.0, pnl_is=500, pnl_oos=400)
    deltas = HypothesisRunner._deltas(baseline, modified)
    # Below the 30-trade floor → must be inconclusive even though Δsharpe = +1.0
    assert HypothesisRunner._verdict(deltas, modified) == "inconclusive"


def test_verdict_uses_OOS_not_full_history():
    """A change that looks great in-sample but is FLAT out-of-sample must NOT ship.
    This is the exact failure mode the refactor is fixing."""
    baseline = _bt(trades_is=120, trades_oos=80, sharpe_is=1.0, sharpe_oos=1.0, pnl_is=500, pnl_oos=500)
    # Modified: huge IS gain (sharpe 1.0 → 3.0), zero OOS gain. Old verdict said ship; new says no.
    modified = _bt(trades_is=120, trades_oos=80, sharpe_is=3.0, sharpe_oos=1.0, pnl_is=2000, pnl_oos=500)
    deltas = HypothesisRunner._deltas(baseline, modified)
    assert HypothesisRunner._verdict(deltas, modified) == "inconclusive"


def test_deltas_contain_oos_and_is_breakdown_for_kb_evidence():
    baseline = _bt(120, 80, 1.0, 1.0, 500, 500)
    modified = _bt(120, 80, 1.10, 1.20, 700, 800)
    d = HypothesisRunner._deltas(baseline, modified)
    # OOS deltas (what the verdict reads)
    assert d["oos_sharpe_delta"] == round(0.20, 3)
    assert d["oos_pnl_delta"]    == 300
    # IS deltas (context for KB evidence)
    assert d["is_sharpe_delta"]  == round(0.10, 3)
    assert d["is_pnl_delta"]     == 200
    # Aggregate deltas (back-compat — existing KB readers still expect these keys)
    assert "sharpe_delta" in d and "pnl_delta" in d
