"""Tests for the 60d backfill seed script (Phase 4a item 0b)."""
import json
import os
import tempfile
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.seed_simulated_trades import (
    transform_backtest_row,
    seed_simulated_trades,
)


def test_transform_iron_condor_win():
    row = {
        "date":   pd.Timestamp("2026-04-15").date(),
        "regime": "choppy_low_vol",
        "play":   "iron_condor",
        "tradeable": True,
        "vix":    14.5,
        "ivr":    35.0,
        "adx":    18.0,
        "ma200_dist": 2.1,
        "outcome": "win",
        "pnl":     130,
        "confidence": 0.7,
    }
    rec = transform_backtest_row(row, seq=1)
    assert rec["strategy"] == "iron_condor"
    assert rec["dte_bucket"] == "45DTE"
    assert rec["book"] == "disciplined"
    assert rec["simulated"] is True
    assert rec["outcome"] == "win"
    assert rec["pnl_dollars"] == 130
    assert rec["trade_id"].startswith("sim_")
    assert rec["ticker"] == "SPY"
    assert rec["notes_entry"] == "[SEEDED-BACKFILL]"


def test_transform_skip_row_returns_none():
    row = {
        "date": pd.Timestamp("2026-04-15").date(),
        "play": "skip", "tradeable": False, "outcome": "skip", "pnl": 0,
        "regime": "trending_high_vol", "vix": 22, "ivr": 60, "adx": 28,
        "ma200_dist": 5.0, "confidence": 0.3,
    }
    assert transform_backtest_row(row, seq=1) is None


def test_transform_bull_debit_win():
    row = {
        "date":   pd.Timestamp("2026-04-15").date(),
        "regime": "trending_up_calm",
        "play":   "bull_debit",
        "tradeable": True,
        "vix":    15.0, "ivr": 30.0, "adx": 28.0, "ma200_dist": 3.5,
        "outcome": "win",
        "pnl":    150,
        "confidence": 0.8,
    }
    rec = transform_backtest_row(row, seq=2)
    assert rec["strategy"] == "debit_spread"
    assert rec["direction"] == "BULLISH"
    assert rec["dte_bucket"] == "45DTE"


def test_seed_writes_jsonfile_idempotent_guard(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr("config.LOG_DIR", d)
        target = os.path.join(d, "simulated_trades.json")

        fake_df = pd.DataFrame([
            {"date": pd.Timestamp("2026-04-15").date(), "regime": "choppy_low_vol",
             "play": "iron_condor", "tradeable": True, "vix": 14.5, "ivr": 35.0,
             "adx": 18.0, "ma200_dist": 2.1, "outcome": "win", "pnl": 130,
             "confidence": 0.7},
            {"date": pd.Timestamp("2026-04-16").date(), "regime": "choppy_low_vol",
             "play": "iron_condor", "tradeable": True, "vix": 14.0, "ivr": 33.0,
             "adx": 17.0, "ma200_dist": 1.9, "outcome": "loss", "pnl": -220,
             "confidence": 0.6},
        ])
        with patch("scripts.seed_simulated_trades._run_backtest", return_value=fake_df):
            n = seed_simulated_trades(days=30)
        assert n == 2
        with open(target) as f:
            rows = json.load(f)
        assert len(rows) == 2
        assert all(r["simulated"] for r in rows)

        # Second call without --force must refuse
        with patch("scripts.seed_simulated_trades._run_backtest", return_value=fake_df):
            with pytest.raises(SystemExit):
                seed_simulated_trades(days=30, force=False)


def test_seed_force_overwrites(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr("config.LOG_DIR", d)
        target = os.path.join(d, "simulated_trades.json")
        with open(target, "w") as f:
            json.dump([{"trade_id": "sim_old", "ticker": "SPY"}], f)

        fake_df = pd.DataFrame([
            {"date": pd.Timestamp("2026-04-15").date(), "regime": "choppy_low_vol",
             "play": "iron_condor", "tradeable": True, "vix": 14.5, "ivr": 35.0,
             "adx": 18.0, "ma200_dist": 2.1, "outcome": "win", "pnl": 130,
             "confidence": 0.7},
        ])
        with patch("scripts.seed_simulated_trades._run_backtest", return_value=fake_df):
            n = seed_simulated_trades(days=30, force=True)
        assert n == 1
        with open(target) as f:
            rows = json.load(f)
        assert all(r["trade_id"].startswith("sim_") for r in rows)
        assert not any(r["trade_id"] == "sim_old" for r in rows)
