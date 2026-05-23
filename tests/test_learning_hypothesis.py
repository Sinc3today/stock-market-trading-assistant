"""
tests/test_learning_hypothesis.py -- HypothesisEngine + HypothesisRunner.
Mocks Claude for the engine; injects a fake backtest for the runner.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.hypothesis_engine import HypothesisEngine
from learning.hypothesis_runner import HypothesisRunner
from learning.knowledge_base    import KnowledgeBase


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


# ── HypothesisEngine ──────────────────────────────────

def test_engine_valid_proposal_saved(iso, monkeypatch):
    fake = json.dumps({
        "status":         "propose",
        "title":          "Raise ADX_TREND_MIN to 27.0",
        "rationale":      "Last 30d show 3 weak-trend losses at ADX 25-26.",
        "module":         "signals.regime_detector",
        "var":            "ADX_TREND_MIN",
        "current_value":  25.0,
        "proposed_value": 27.0,
        "expected_impact": "Fewer trending signals, better win rate.",
        "confidence":     0.65,
    })
    e = HypothesisEngine(api_key="fake")
    monkeypatch.setattr(e, "_call_claude", lambda prompt: fake)

    spec = e.propose_weekly()
    assert spec is not None
    assert spec["status"] == "proposed"
    assert spec["var"]    == "ADX_TREND_MIN"
    assert spec["proposed_value"] == 27.0
    # File written
    assert os.path.exists(os.path.join(str(iso), "learning", "hypotheses", f"{spec['id']}.json"))
    # KB has a "hypothesis" entry
    assert any(r["category"] == "hypothesis" for r in KnowledgeBase().all())


def test_engine_rejects_out_of_range(iso, monkeypatch):
    fake = json.dumps({
        "status":         "propose",
        "title":          "Crank ADX to 99",
        "rationale":      "Because.",
        "module":         "signals.regime_detector",
        "var":            "ADX_TREND_MIN",
        "current_value":  25.0,
        "proposed_value": 99.0,
        "expected_impact": "wreck the bot",
        "confidence":     0.9,
    })
    e = HypothesisEngine(api_key="fake")
    monkeypatch.setattr(e, "_call_claude", lambda prompt: fake)
    spec = e.propose_weekly()
    assert spec is None


def test_engine_rejects_unwhitelisted_module(iso, monkeypatch):
    fake = json.dumps({
        "status":         "propose",
        "title":          "Patch random module",
        "rationale":      "Because.",
        "module":         "some.evil.module",
        "var":            "FORMAT_HARD_DRIVE",
        "current_value":  False,
        "proposed_value": True,
        "expected_impact": "rm -rf /",
        "confidence":     1.0,
    })
    e = HypothesisEngine(api_key="fake")
    monkeypatch.setattr(e, "_call_claude", lambda prompt: fake)
    assert e.propose_weekly() is None


def test_engine_status_none_writes_no_proposal_file(iso, monkeypatch):
    fake = json.dumps({"status": "none", "rationale": "Not enough data yet."})
    e = HypothesisEngine(api_key="fake")
    monkeypatch.setattr(e, "_call_claude", lambda prompt: fake)
    assert e.propose_weekly() is None
    no_prop_file = os.path.join(
        str(iso), "learning", "hypotheses",
        f"{date.today().isoformat()}_no_proposal.json",
    )
    assert os.path.exists(no_prop_file)


# ── HypothesisRunner ──────────────────────────────────

def _write_pending_spec(iso, value=27.0):
    spec = {
        "id":              f"hyp_{date.today().isoformat()}_test",
        "date":            date.today().isoformat(),
        "title":           "Raise ADX to 27",
        "rationale":       "test",
        "module":          "signals.regime_detector",
        "var":             "ADX_TREND_MIN",
        "current_value":   25.0,
        "proposed_value":  value,
        "expected_impact": "x",
        "confidence":      0.6,
        "status":          "proposed",
        "backtest":        None,
    }
    path = os.path.join(str(iso), "learning", "hypotheses", f"{spec['id']}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(spec, f, indent=2)
    return spec, path


def _make_bt(trades_is, trades_oos, sharpe_is, sharpe_oos, pnl_is, pnl_oos, win_rate=50.0):
    """Build a backtest result dict with IS/OOS split (new shape)."""
    return {
        "trades":   trades_is + trades_oos,
        "win_rate": win_rate,
        "pnl":      pnl_is + pnl_oos,
        "sharpe":   round((sharpe_is + sharpe_oos) / 2, 3),
        "is":  {"trades": trades_is,  "win_rate": win_rate, "pnl": pnl_is,  "sharpe": sharpe_is},
        "oos": {"trades": trades_oos, "win_rate": win_rate, "pnl": pnl_oos, "sharpe": sharpe_oos},
    }


def _bt_fn_accept(override):
    """Baseline vs modified: modified wins on OOS Sharpe + OOS PnL above accept thresholds."""
    if override is None:
        return _make_bt(trades_is=60, trades_oos=40, sharpe_is=1.50, sharpe_oos=1.50,
                        pnl_is=600, pnl_oos=400, win_rate=50.0)
    return     _make_bt(trades_is=50, trades_oos=35, sharpe_is=1.50, sharpe_oos=1.75,
                        pnl_is=600, pnl_oos=800, win_rate=55.0)


def _bt_fn_reject(override):
    if override is None:
        return _make_bt(trades_is=60, trades_oos=40, sharpe_is=1.50, sharpe_oos=1.50,
                        pnl_is=600, pnl_oos=400, win_rate=50.0)
    return     _make_bt(trades_is=55, trades_oos=35, sharpe_is=1.50, sharpe_oos=1.20,
                        pnl_is=400, pnl_oos=100, win_rate=45.0)


def _bt_fn_inconclusive(override):
    if override is None:
        return _make_bt(trades_is=60, trades_oos=40, sharpe_is=1.50, sharpe_oos=1.50,
                        pnl_is=600, pnl_oos=400, win_rate=50.0)
    return     _make_bt(trades_is=58, trades_oos=40, sharpe_is=1.50, sharpe_oos=1.53,
                        pnl_is=620, pnl_oos=450, win_rate=50.5)


def test_runner_accepts(iso):
    _write_pending_spec(iso)
    runner = HypothesisRunner(backtest_fn=_bt_fn_accept)
    ran = runner.run_pending()
    assert len(ran) == 1
    assert ran[0]["status"] == "accepted"
    assert any(r["category"] == "backtest_result" for r in KnowledgeBase().all())


def test_runner_rejects(iso):
    _write_pending_spec(iso)
    runner = HypothesisRunner(backtest_fn=_bt_fn_reject)
    ran = runner.run_pending()
    assert ran[0]["status"] == "rejected"


def test_runner_inconclusive(iso):
    _write_pending_spec(iso)
    runner = HypothesisRunner(backtest_fn=_bt_fn_inconclusive)
    ran = runner.run_pending()
    assert ran[0]["status"] == "inconclusive"


def test_runner_post_fn_fires_on_accept(iso):
    """Accept verdict -> post_fn called with a promote-command hint."""
    _write_pending_spec(iso)
    captured: list[str] = []
    runner = HypothesisRunner(
        backtest_fn = _bt_fn_accept,
        post_fn     = lambda m: captured.append(m),
    )
    ran = runner.run_pending()
    assert ran[0]["status"] == "accepted"
    assert len(captured) == 1
    assert "python -m learning.promote" in captured[0]


def test_runner_post_fn_skipped_on_inconclusive(iso):
    _write_pending_spec(iso)
    captured: list[str] = []
    runner = HypothesisRunner(
        backtest_fn = _bt_fn_inconclusive,
        post_fn     = lambda m: captured.append(m),
    )
    runner.run_pending()
    assert captured == []


def test_runner_skips_already_processed(iso):
    spec, path = _write_pending_spec(iso)
    HypothesisRunner(backtest_fn=_bt_fn_accept).run_pending()
    # Second invocation should not re-process
    second = HypothesisRunner(backtest_fn=_bt_fn_accept).run_pending()
    assert second == []


def test_runner_rejects_non_whitelisted(iso):
    spec = {
        "id":              "hyp_evil",
        "date":            "2026-01-01",
        "title":           "evil",
        "module":          "evil.module",
        "var":             "X",
        "current_value":   1, "proposed_value": 2,
        "status":          "proposed",
    }
    path = os.path.join(str(iso), "learning", "hypotheses", "hyp_evil.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(spec, f, indent=2)
    HypothesisRunner(backtest_fn=_bt_fn_accept).run_pending()
    # spec file should be marked error
    with open(path) as f:
        out = json.load(f)
    assert out["status"] == "error"
