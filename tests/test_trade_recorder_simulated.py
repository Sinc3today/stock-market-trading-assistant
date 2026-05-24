"""Tests for TradeRecorder simulated-trade support (Phase 4a item 0)."""
import json
import os
import tempfile
from unittest.mock import patch
import pytest

from journal.trade_recorder import TradeRecorder


@pytest.fixture
def temp_log_dir(monkeypatch):
    """Redirect config.LOG_DIR to a temp directory."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr("config.LOG_DIR", d)
        yield d


def _write_real(log_dir, trades):
    with open(os.path.join(log_dir, "trades.json"), "w") as f:
        json.dump(trades, f)


def _write_sim(log_dir, trades):
    with open(os.path.join(log_dir, "simulated_trades.json"), "w") as f:
        json.dump(trades, f)


def test_get_all_trades_excludes_simulated_by_default(temp_log_dir):
    _write_real(temp_log_dir, [{"trade_id": "AAAA0001", "ticker": "SPY"}])
    _write_sim(temp_log_dir,  [{"trade_id": "sim_xx01", "ticker": "SPY", "simulated": True}])
    tr = TradeRecorder()
    rows = tr.get_all_trades()
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "AAAA0001"


def test_get_trades_by_include_simulated_true(temp_log_dir):
    _write_real(temp_log_dir, [{"trade_id": "AAAA0001", "ticker": "SPY",
                                "strategy": "iron_condor", "dte_bucket": "45DTE",
                                "book": "disciplined", "outcome": "win"}])
    _write_sim(temp_log_dir,  [{"trade_id": "sim_xx01", "ticker": "SPY",
                                "strategy": "iron_condor", "dte_bucket": "45DTE",
                                "book": "disciplined", "outcome": "win",
                                "simulated": True}])
    tr = TradeRecorder()
    rows = tr.get_trades_by(strategy="iron_condor", dte_bucket="45DTE",
                            book="disciplined", include_simulated=True)
    assert len(rows) == 2
    assert any(r.get("simulated") for r in rows)
    assert any(not r.get("simulated") for r in rows)


def test_simulated_file_missing_is_ok(temp_log_dir):
    _write_real(temp_log_dir, [{"trade_id": "AAAA0001", "ticker": "SPY"}])
    # No simulated_trades.json
    tr = TradeRecorder()
    rows = tr.get_trades_by(include_simulated=True)
    assert len(rows) == 1


def test_summary_stats_excludes_simulated(temp_log_dir):
    """P&L reports must not include simulated trades in totals."""
    _write_real(temp_log_dir, [{
        "trade_id": "AAAA0001", "outcome": "win", "pnl_dollars": 130, "pnl_pct": 0.65,
    }])
    _write_sim(temp_log_dir, [{
        "trade_id": "sim_xx01", "outcome": "win", "pnl_dollars": 999, "pnl_pct": 5.0,
        "simulated": True,
    }])
    tr = TradeRecorder()
    stats = tr.get_summary_stats()
    assert stats["total_pnl"] == 130.0  # NOT 1129
