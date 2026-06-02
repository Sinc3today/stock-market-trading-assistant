# tests/test_exit_feasibility.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from signals.exit_feasibility import assign_book


def test_disciplined_when_target_and_rr_clear(monkeypatch):
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "1-3DTE"): {"min_target_dollars": 50.0, "min_rr": 0.2}})
    # max_profit=100, pt=0.7 → target=70 ≥ 50; rr=100/400=0.25 ≥ 0.2 → disciplined
    assert assign_book("iron_condor", "1-3DTE", 100.0, 400.0, profit_target_pct=0.7) == "disciplined"


def test_learning_when_target_too_small(monkeypatch):
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "0DTE"): {"min_target_dollars": 50.0, "min_rr": 0.0}})
    # max_profit=6, pt=0.7 → target=4.2 < 50 → learning (the EOD 0DTE IC case)
    assert assign_book("iron_condor", "0DTE", 6.0, 494.0, profit_target_pct=0.7) == "learning"


def test_learning_when_rr_too_low(monkeypatch):
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "1-3DTE"): {"min_target_dollars": 0.0, "min_rr": 0.5}})
    assert assign_book("iron_condor", "1-3DTE", 100.0, 400.0, profit_target_pct=0.7) == "learning"


def test_unconfigured_combo_defaults_permissive(monkeypatch):
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY", {})
    assert assign_book("call_debit_spread", "0DTE", 1.0, 999.0, profit_target_pct=0.7) == "disciplined"


def test_zero_max_loss_routes_learning(monkeypatch):
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "0DTE"): {"min_target_dollars": 0.0, "min_rr": 0.1}})
    assert assign_book("iron_condor", "0DTE", 100.0, 0.0, profit_target_pct=0.7) == "learning"


def test_none_max_profit_does_not_raise(monkeypatch):
    """None max_profit must not raise; with min_target_dollars=50 → target 0 < 50 → learning."""
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "0DTE"): {"min_target_dollars": 50.0, "min_rr": 0.0}})
    result = assign_book("iron_condor", "0DTE", None, 400.0, profit_target_pct=0.7)
    assert isinstance(result, str)
    assert result == "learning"


def test_negative_max_profit_routes_learning(monkeypatch):
    """Degenerate pricing (negative max_profit) routes to learning by design."""
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY", {})
    # target = 0.7 * -5.0 = -3.5 < 0.0 (permissive min_target_dollars) → learning
    assert assign_book("call_debit_spread", "0DTE", -5.0, 100.0, profit_target_pct=0.7) == "learning"


def test_zero_max_loss_disciplined_under_permissive(monkeypatch):
    """Zero max_loss → rr=0.0; permissive min_rr=0.0; target 70>=0 → disciplined."""
    import config
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY", {})
    assert assign_book("iron_condor", "0DTE", 100.0, 0.0, profit_target_pct=0.7) == "disciplined"


# ── Calibrated policy (2026-06-02): 0DTE sandboxed, 1-3DTE disciplined ──

def test_calibrated_0dte_routes_to_learning_sandbox():
    """Real config: every 0DTE structure routes to learning (prohibitive bar),
    regardless of how rich its pricing looks — 0DTE is a confirmed OOS loser."""
    for strat in ("call_debit_spread", "put_debit_spread", "iron_condor"):
        # even a generous 0DTE structure (target 140, rr 2.0) must go learning
        assert assign_book(strat, "0DTE", 200.0, 100.0, profit_target_pct=0.7) == "learning"


def test_calibrated_1_3dte_routes_disciplined():
    """Real config: 1-3DTE is permissive → disciplined (the marginal winner)."""
    for strat in ("call_debit_spread", "put_debit_spread", "iron_condor"):
        assert assign_book(strat, "1-3DTE", 100.0, 400.0, profit_target_pct=0.7) == "disciplined"
