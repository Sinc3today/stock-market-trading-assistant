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
