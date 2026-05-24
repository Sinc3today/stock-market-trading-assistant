# Phase 4a — Learning Loop Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the learning loop's signal quality, cost profile, and resilience — zero live trading-behavior change.

**Architecture:** Seven hygiene items grouped into 8 sequential tasks: (0a, 0b) backfill foundation, (1) caching, (2) per-sub-strategy accuracy, (3) KB validator, (4) anomaly-routed reflector, (5) regime-drift learner, (6) integration smoke. All changes are in the learning loop — none touch the trade-execution path. Phase 3's `INTRADAY_PAPER_BROKER_ENABLED` kill switch remains the sole live-behavior gate.

**Tech Stack:** Python 3.11+, pytest, loguru, Anthropic SDK (cache_control), Ollama HTTP client, pandas, existing project libs.

**Spec:** `docs/superpowers/specs/2026-05-23-phase4a-learning-loop-hygiene-design.md`

**Branch baseline:** 771 tests passing (Phase 3 end).

---

## File Structure

### Files Created

| Path | Responsibility |
|---|---|
| `scripts/seed_simulated_trades.py` | One-shot ops tool — runs daily backtest over 60 trading days, transforms results into TradeRecorder schema, persists to `logs/simulated_trades.json` |
| `logs/simulated_trades.json` | Synthetic trade records for 45DTE strategies (single JSON array, same shape as `logs/trades.json`); created by seed script |
| `learning/kb_validator.py` | KB-entry validator (Item 3 confidence cap + Item 4 evidence citation) — pure function, importable by reflector |
| `learning/anomaly_detector.py` | Anomaly-detection logic for Item 5 routing — pure function: `is_anomalous_day(facts) -> bool` |
| `tests/test_trade_recorder_simulated.py` | Item 0a tests |
| `tests/test_simulated_trades_seed.py` | Item 0b seed-script tests |
| `tests/test_llm_client_caching.py` | Item 1 cache_control wiring tests |
| `tests/test_predictions_per_substrategy.py` | Item 2 per-sub-strategy accuracy tests |
| `tests/test_kb_validator.py` | Items 3+4 validator tests |
| `tests/test_anomaly_detector.py` | Item 5 anomaly trigger tests |
| `tests/test_reflector_routing.py` | Item 5 routing integration tests |
| `tests/test_off_hours_regime_drift.py` | Item 6 regime drift tests |

### Files Modified

| Path | What changes |
|---|---|
| `journal/trade_recorder.py` | `_load()` unions trades.json + simulated_trades.json; `get_trades_by()` gains `include_simulated=False` param; `simulated` field documented in schema |
| `config.py` | Add 5 Phase 4a constants (anomaly triggers + regime drift threshold) |
| `data/llm_client.py` | `call_llm()` gains optional `cache_static_system: bool = False` to mark system prompt as cacheable; route by `model_preference: str = "sonnet_first" \| "phi4_first"` |
| `learning/predictions.py` | `accuracy()` gains optional `by_substrategy: bool = False`, returns dict `{strategy:dte_bucket:book → accuracy}` when set |
| `learning/reflector.py` | Use `kb_validator.validate()` post-Sonnet; route via `anomaly_detector.is_anomalous_day()` (Item 5) |
| `learning/hypothesis_engine.py` | Replace direct `requests.post` with `call_llm`; consume per-sub-strategy `rolling_accuracy` dict |
| `learning/off_hours_learner.py` | Replace near-miss algorithm with regime-drift detection; also moved to `call_llm` |
| `alerts/ai_advisor.py` | Switch to `call_llm` with caching (best-effort — only if it currently uses direct requests) |

---

## Task 1 — Item 0a: TradeRecorder simulated-flag support

**Files:**
- Modify: `journal/trade_recorder.py`
- Test: `tests/test_trade_recorder_simulated.py`

### Steps

- [ ] **Step 1: Write the failing test for unioning simulated trades**

Create `tests/test_trade_recorder_simulated.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_trade_recorder_simulated.py -v
```
Expected: 4 FAIL — `include_simulated` param doesn't exist yet, `_load()` doesn't read simulated file.

- [ ] **Step 3: Modify `journal/trade_recorder.py` — add simulated support**

In `TradeRecorder.__init__`, after `self.trades_path = ...`, add:

```python
        self.simulated_path = os.path.join(config.LOG_DIR, "simulated_trades.json")
```

Replace the `_load()` method with a non-unioning version (existing behavior) and add a new `_load_simulated()`:

```python
    def _load(self) -> list:
        if not os.path.exists(self.trades_path):
            return []
        try:
            with open(self.trades_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"TradeRecorder: failed to load {self.trades_path}: {e}")
            return []

    def _load_simulated(self) -> list:
        """Load synthetic trades from simulated_trades.json. Returns [] if missing.

        Simulated trades have `simulated: True` flag. Used by learning-loop
        consumers (hypothesis_engine, off_hours_learner, rolling_accuracy)
        that explicitly pass include_simulated=True.
        """
        if not os.path.exists(self.simulated_path):
            return []
        try:
            with open(self.simulated_path, "r") as f:
                rows = json.load(f)
            # Defensive: enforce the flag so callers can rely on it
            for r in rows:
                r.setdefault("simulated", True)
            return rows
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"TradeRecorder: failed to load {self.simulated_path}: {e}")
            return []
```

Update `get_trades_by()`:

```python
    def get_trades_by(self, *, strategy: str | None = None,
                      dte_bucket: str | None = None,
                      book: str | None = None,
                      exit_reason: str | None = None,
                      include_simulated: bool = False) -> list:
        """Filter trades by optional tag values. Trades that lack a tag are
        EXCLUDED from filters that specify that tag — old (untagged) trades
        don't participate in strategy/book/dte_bucket searches.

        include_simulated=True unions in synthetic trades from simulated_trades.json.
        Default False keeps P&L / dashboard callers safe.

        No-filter call returns all trades.
        """
        rows = self.get_all_trades()
        if include_simulated:
            rows = rows + self._load_simulated()
        if strategy is not None:
            rows = [t for t in rows if t.get("strategy") == strategy]
        if dte_bucket is not None:
            rows = [t for t in rows if t.get("dte_bucket") == dte_bucket]
        if book is not None:
            rows = [t for t in rows if t.get("book") == book]
        if exit_reason is not None:
            rows = [t for t in rows if t.get("exit_reason") == exit_reason]
        return rows
```

Confirm `get_all_trades()` and `get_summary_stats()` are unchanged — they read only real trades. They MUST NOT include simulated by default to protect P&L reporting.

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_trade_recorder_simulated.py -v
```
Expected: 4 PASS.

- [ ] **Step 5: Confirm baseline tests still pass**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -20
```
Expected: 775 passing (771 baseline + 4 new), 0 failures.

- [ ] **Step 6: Commit**

```bash
git add journal/trade_recorder.py tests/test_trade_recorder_simulated.py
git commit -m "feat(phase4a-0a): TradeRecorder reads simulated_trades.json on opt-in

include_simulated=False by default keeps P&L/dashboard reports safe.
Learning-loop consumers (hypothesis_engine, off_hours_learner) opt in.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — Item 0b: Backfill seed script + first execution

**Files:**
- Create: `scripts/seed_simulated_trades.py`
- Test:   `tests/test_simulated_trades_seed.py`

### Steps

- [ ] **Step 1: Write the failing test**

Create `tests/test_simulated_trades_seed.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_simulated_trades_seed.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the seed script**

Create `scripts/seed_simulated_trades.py`:

```python
"""Phase 4a item 0b — backfill seed script.

Runs `backtests/spy_daily_backtest.py` over the last ~90 calendar days
(~60 trading days), transforms each backtested trading day into a
TradeRecorder-schema record, and writes them to
`logs/simulated_trades.json` with `simulated: True`.

Idempotency: refuses to overwrite an existing simulated_trades.json
unless --force is passed (creates a .bak backup first).

Usage:
    python -m scripts.seed_simulated_trades
    python -m scripts.seed_simulated_trades --days 90 --force
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import date, timedelta

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# Map daily-backtest "play" → (strategy, direction) for TradeRecorder schema
PLAY_TO_STRATEGY = {
    "iron_condor": ("iron_condor", "NEUTRAL"),
    "bull_debit":  ("debit_spread",  "BULLISH"),
    "bear_debit":  ("debit_spread",  "BEARISH"),
    "bull_credit": ("credit_spread", "BULLISH"),
    "bear_credit": ("credit_spread", "BEARISH"),
}


def _run_backtest(days: int) -> pd.DataFrame:
    """Run spy_daily_backtest's SPYBacktest over the last `days` calendar days.

    Isolated so tests can monkey-patch it.
    """
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar

    # Convert days → years (rough), with a floor of 0.3 years (~110 days)
    years = max(0.3, days / 365.0)
    loader = BacktestDataLoader()
    spy_df, vix_df = loader.load(years=years, source="local")
    cal = EventCalendar()
    bt = SPYBacktest(spy_df, vix_df, cal, years=years)
    df = bt.run()
    # Trim to the requested window (in case backtest loads more)
    cutoff = date.today() - timedelta(days=days)
    return df[df["date"] >= cutoff]


def transform_backtest_row(row: dict, seq: int) -> dict | None:
    """Transform one backtest-result row → TradeRecorder-schema record.

    Returns None for skipped/non-tradeable days.
    """
    if not row.get("tradeable") or row.get("play") == "skip":
        return None
    play = row.get("play", "")
    if play not in PLAY_TO_STRATEGY:
        return None
    strategy, direction = PLAY_TO_STRATEGY[play]

    # Build the record. Phase 4a uses placeholder option-pricing fields
    # (entry_price/exit_price are unknown for daily backtest — it only
    # tracks regime → outcome → fixed pnl). We populate what we can and
    # leave pricing fields null. Phase 4b's Path 1 backfill replaces these.
    sim_id = f"sim_{uuid.uuid4().hex[:8]}"
    entry_date = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
    outcome = row.get("outcome", "skip")
    pnl_dollars = float(row.get("pnl", 0))

    return {
        # Identity
        "trade_id":   sim_id,
        "ticker":     "SPY",
        "trade_type": "option_spread",
        "strategy":   strategy,
        "direction":  direction,
        "mode":       "swing",

        # Entry (placeholder — daily backtest does not track per-leg prices)
        "entry_price": None,
        "size":        1,
        "entry_date":  entry_date,
        "entry_value": None,

        # Spread fields (placeholder)
        "legs":       [],
        "max_profit": None,
        "max_loss":   None,

        # Alert link
        "alert_timestamp": None,
        "alert_score":     None,

        # Exit
        "exit_price": None,
        "exit_date":  entry_date,    # daily backtest treats each day as one-shot
        "exit_value": None,

        # Outcome
        "outcome":          outcome,
        "pnl_dollars":      pnl_dollars,
        "pnl_pct":          None,
        "pnl_per_contract": None,
        "exit_reason":      "backtest_outcome",

        # Notes
        "notes_entry": "[SEEDED-BACKFILL]",
        "notes_exit":  "",
        "lessons":     "",

        # Phase 2a tags
        "dte_bucket": "45DTE",
        "book":       "disciplined",

        # Phase 4a item 0 flag
        "simulated":  True,

        # Backtest context (extra fields for learning loop)
        "_backtest_regime": row.get("regime"),
        "_backtest_vix":    row.get("vix"),
        "_backtest_adx":    row.get("adx"),
    }


def seed_simulated_trades(days: int = 90, force: bool = False) -> int:
    """Run the backtest, transform rows, write `simulated_trades.json`.

    Returns the count of records written.
    """
    target = os.path.join(config.LOG_DIR, "simulated_trades.json")
    if os.path.exists(target) and not force:
        logger.error(
            f"{target} already exists. Re-run with --force to overwrite "
            f"(a .bak backup will be created first)."
        )
        sys.exit(2)
    if os.path.exists(target) and force:
        shutil.copy(target, target + ".bak")
        logger.info(f"Backed up existing file to {target}.bak")

    df = _run_backtest(days)
    records: list[dict] = []
    seq = 0
    for _, row in df.iterrows():
        rec = transform_backtest_row(row.to_dict(), seq)
        if rec is not None:
            records.append(rec)
            seq += 1

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        json.dump(records, f, indent=2, default=str)
    logger.info(f"Seeded {len(records)} simulated trades → {target}")
    return len(records)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days",  type=int, default=90,
                   help="Calendar days to look back (default 90 ≈ 60 trading days)")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing simulated_trades.json")
    args = p.parse_args()
    n = seed_simulated_trades(days=args.days, force=args.force)
    print(f"Seeded {n} records.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_simulated_trades_seed.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Run the seed script for real (first-time population)**

```bash
python -m scripts.seed_simulated_trades --days 90
```
Expected output: `Seeded N records.` where N is between ~30 and ~60 depending on regime distribution.

Verify the file:
```bash
ls -la logs/simulated_trades.json
python -c "import json; data=json.load(open('logs/simulated_trades.json')); print(f'{len(data)} records'); print(f'simulated flag set: {all(r[\"simulated\"] for r in data)}'); print(f'strategies: {set(r[\"strategy\"] for r in data)}')"
```
Expected: file exists, all records have `simulated: True`, strategies include at least `iron_condor`.

- [ ] **Step 6: Confirm baseline tests still pass**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
```
Expected: 780 passing (775 prior + 5 new), 0 failures.

- [ ] **Step 7: Commit**

```bash
git add scripts/seed_simulated_trades.py tests/test_simulated_trades_seed.py logs/simulated_trades.json
git commit -m "feat(phase4a-0b): 60d backfill seed script + initial seed

Reads backtests/spy_daily_backtest output for last 90 calendar days,
transforms to TradeRecorder schema, persists to logs/simulated_trades.json
with simulated=True. Phase 4a item 0 foundation for items 2 and 5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — Item 1: Prompt caching across all Sonnet callers

**Files:**
- Modify: `data/llm_client.py`
- Modify: `learning/reflector.py`
- Modify: `learning/hypothesis_engine.py`
- Modify: `learning/off_hours_learner.py`
- Modify: `alerts/ai_advisor.py` (only if it makes direct API calls)
- Test:   `tests/test_llm_client_caching.py`

### Steps

- [ ] **Step 1: Read the current `data/llm_client.py` to understand the existing signature**

```bash
cat data/llm_client.py
```

Implementer must read this file. The plan assumes `call_llm()` exists and accepts `system`, `user`, `anthropic_model`, `api_key`, `max_tokens` kwargs (per the reflector usage at `learning/reflector.py:176-182`). If the signature differs, adapt accordingly.

- [ ] **Step 2: Write the failing test**

Create `tests/test_llm_client_caching.py`:

```python
"""Tests for cache_control wiring in data/llm_client.call_llm (Phase 4a item 1)."""
import os
import sys
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.llm_client import call_llm


@pytest.fixture
def mock_anthropic_post():
    """Mock requests.post used by the Anthropic path."""
    with patch("data.llm_client.requests.post") as mp:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": [{"type": "text", "text": "ok"}]}
        mock_resp.raise_for_status = MagicMock()
        mp.return_value = mock_resp
        yield mp


def test_call_llm_default_no_cache_control(mock_anthropic_post):
    """Default behavior — no cache_control sent."""
    call_llm(system="sys", user="usr", anthropic_model="claude-sonnet-4-6",
             api_key="test_key", max_tokens=100)
    payload = mock_anthropic_post.call_args.kwargs["json"]
    # System should be plain text, no cache marker
    assert payload["system"] == "sys" or (
        isinstance(payload["system"], list) and
        all("cache_control" not in b for b in payload["system"])
    )


def test_call_llm_cache_static_system_true_marks_system_cacheable(mock_anthropic_post):
    """When cache_static_system=True, system prompt must include ephemeral cache_control."""
    call_llm(system="sys", user="usr", anthropic_model="claude-sonnet-4-6",
             api_key="test_key", max_tokens=100, cache_static_system=True)
    payload = mock_anthropic_post.call_args.kwargs["json"]
    # Expect system as a list of blocks with cache_control on the static one
    assert isinstance(payload["system"], list)
    assert any(
        b.get("cache_control", {}).get("type") == "ephemeral"
        for b in payload["system"]
    )


def test_call_llm_routes_phi4_first_when_requested():
    """model_preference='phi4_first' must try Ollama before Anthropic."""
    with patch("data.llm_client._call_ollama", return_value="phi4 reply") as ollama_mock, \
         patch("data.llm_client._call_anthropic") as anthropic_mock:
        result = call_llm(system="sys", user="usr",
                          anthropic_model="claude-sonnet-4-6",
                          api_key="test_key", max_tokens=100,
                          model_preference="phi4_first")
        assert result == "phi4 reply"
        ollama_mock.assert_called_once()
        anthropic_mock.assert_not_called()


def test_call_llm_phi4_first_falls_back_on_failure():
    """phi4_first must escalate to Sonnet on any phi4 exception."""
    with patch("data.llm_client._call_ollama", side_effect=RuntimeError("ollama down")), \
         patch("data.llm_client._call_anthropic", return_value="sonnet fallback") as anthropic_mock:
        result = call_llm(system="sys", user="usr",
                          anthropic_model="claude-sonnet-4-6",
                          api_key="test_key", max_tokens=100,
                          model_preference="phi4_first")
        assert result == "sonnet fallback"
        anthropic_mock.assert_called_once()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_llm_client_caching.py -v
```
Expected: 4 FAIL — `cache_static_system` and `model_preference` params don't exist.

- [ ] **Step 4: Modify `data/llm_client.py`**

Implementer must:

1. Read the existing `call_llm` body.
2. Refactor internal Anthropic call into a private `_call_anthropic(system, user, model, api_key, max_tokens, cache_static_system) -> str` function. When `cache_static_system=True`, pass `system` as a list block:
   ```python
   system_payload = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}] \
                    if cache_static_system else system
   ```
3. Refactor internal Ollama call into a private `_call_ollama(system, user, max_tokens) -> str` function.
4. Add `cache_static_system: bool = False` and `model_preference: str = "sonnet_first"` to `call_llm` signature.
5. Routing logic:
   ```python
   if model_preference == "phi4_first":
       try:
           return _call_ollama(system, user, max_tokens)
       except Exception as e:
           logger.warning(f"phi4 failed ({e}); escalating to Sonnet")
           return _call_anthropic(system, user, anthropic_model, api_key, max_tokens, cache_static_system)
   else:  # "sonnet_first" (existing default)
       try:
           return _call_anthropic(system, user, anthropic_model, api_key, max_tokens, cache_static_system)
       except Exception as e:
           logger.warning(f"Sonnet failed ({e}); falling back to phi4")
           return _call_ollama(system, user, max_tokens)
   ```

- [ ] **Step 5: Run llm_client tests to verify pass**

```bash
pytest tests/test_llm_client_caching.py -v
```
Expected: 4 PASS.

- [ ] **Step 6: Wire caching into `learning/reflector.py`**

Modify `Reflector._call_claude` (around line 172):

```python
    def _call_claude(self, prompt: str) -> str:
        from data.llm_client import call_llm
        return call_llm(
            system               = REFLECTOR_SYSTEM,
            user                 = prompt,
            anthropic_model      = CLAUDE_MODEL,
            api_key              = self.api_key,
            max_tokens           = 1500,
            cache_static_system  = True,    # Phase 4a item 1
        )
```

- [ ] **Step 7: Refactor `learning/hypothesis_engine.py` to use `call_llm`**

Replace `_call_claude` (around line 219-247) with:

```python
    def _call_claude(self, prompt: str) -> str:
        if not self.api_key:
            logger.warning("HypothesisEngine: ANTHROPIC_API_KEY missing -- skipping")
            return ""
        from data.llm_client import call_llm
        try:
            return call_llm(
                system               = ENGINE_SYSTEM,
                user                 = prompt,
                anthropic_model      = CLAUDE_MODEL,
                api_key              = self.api_key,
                max_tokens           = 1200,
                cache_static_system  = True,
            )
        except Exception as e:
            logger.error(f"HypothesisEngine Claude call failed: {e}")
            return ""
```

- [ ] **Step 8: Refactor `learning/off_hours_learner.py` Claude call**

Replace `_ask_claude_for_observations` (around line 255-315), keeping the parsing + KB-append logic but replacing the requests.post block:

```python
    def _ask_claude_for_observations(
        self, today_str: str, payload: list[dict]
    ) -> list[str]:
        if not self.api_key:
            logger.info("OffHoursLearner: no API key -- skipping Claude pass")
            return []
        from data.llm_client import call_llm
        try:
            text = call_llm(
                system               = LEARNER_SYSTEM,
                user                 = (
                    f"INPUT ({len(payload)} items):\n"
                    f"{json.dumps(payload, indent=2, default=str)}\n\n"
                    f"Produce JSON now."
                ),
                anthropic_model      = CLAUDE_MODEL,
                api_key              = self.api_key,
                max_tokens           = 1000,
                cache_static_system  = True,
            )
        except Exception as e:
            logger.error(f"OffHoursLearner Claude failed: {e}")
            return []

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

        ids = []
        for raw in parsed.get("kb_entries", []):
            try:
                ids.append(self.kb.append(KBEntry(
                    date       = today_str,
                    category   = raw.get("category", "edge_case"),
                    claim      = raw.get("claim", "")[:500],
                    evidence   = raw.get("evidence", "")[:1000],
                    confidence = float(raw.get("confidence", 0.4)),
                    source     = "off_hours_learner",
                    tags       = list(raw.get("tags") or [])[:8],
                )))
            except Exception as e:
                logger.warning(f"OffHoursLearner: bad entry skipped -- {e}")
        return ids
```

Note: the method's second arg is renamed `near_misses → payload` because Task 7 will repurpose this for regime-drift input. Keeping the signature generic now avoids a rename later.

- [ ] **Step 9: Check and refactor `alerts/ai_advisor.py` if it makes direct API calls**

Read `alerts/ai_advisor.py`. If it uses `requests.post` directly, refactor to `call_llm` with `cache_static_system=True` following the same pattern as Step 7. If it already uses `call_llm`, just add the `cache_static_system=True` flag.

- [ ] **Step 10: Run all tests to confirm nothing broke**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -20
```
Expected: 784 passing (780 prior + 4 new llm tests), 0 failures.

- [ ] **Step 11: Commit**

```bash
git add data/llm_client.py learning/reflector.py learning/hypothesis_engine.py learning/off_hours_learner.py alerts/ai_advisor.py tests/test_llm_client_caching.py
git commit -m "feat(phase4a-1): prompt caching on all Sonnet callers

call_llm gains cache_static_system + model_preference params.
Static system prompts now cached (ephemeral, 5-min TTL).
hypothesis_engine + off_hours_learner refactored to call_llm.
~25-30% cost reduction expected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — Item 2: rolling_accuracy per-sub-strategy

**Files:**
- Modify: `learning/predictions.py`
- Modify: `learning/hypothesis_engine.py`
- Modify: `learning/reflector.py`
- Test:   `tests/test_predictions_per_substrategy.py`

### Steps

- [ ] **Step 1: Implementer reads `learning/predictions.py` to confirm `accuracy()` signature**

```bash
cat learning/predictions.py
```

Plan assumes a method `accuracy(n: int) -> dict` or `float` exists. If it returns float, this task extends it to also support per-sub-strategy when requested.

- [ ] **Step 2: Write the failing test**

Create `tests/test_predictions_per_substrategy.py`:

```python
"""Tests for per-sub-strategy rolling_accuracy (Phase 4a item 2)."""
import os
import sys
import tempfile
import json
from datetime import date, timedelta
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.predictions import PredictionLog


@pytest.fixture
def temp_predlog(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr("config.LOG_DIR", d)
        yield PredictionLog()


def _add_prediction(pl, days_ago: int, direction: str, actual: str,
                    strategy: str | None = None, dte_bucket: str | None = None,
                    book: str | None = None):
    """Append a prediction record with optional Phase 2a tags."""
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    # Implementer: use the existing PredictionLog API for adding +
    # resolving. Helper here is intentionally vague — adapt to real API.
    pl.add(date_str=d, direction=direction, strategy=strategy,
           dte_bucket=dte_bucket, book=book)
    pl.resolve(date_str=d, actual=actual)


def test_accuracy_aggregate_returns_float(temp_predlog):
    pl = temp_predlog
    _add_prediction(pl, 1, "UP", "UP",     strategy="iron_condor", dte_bucket="45DTE", book="disciplined")
    _add_prediction(pl, 2, "UP", "DOWN",   strategy="iron_condor", dte_bucket="45DTE", book="disciplined")
    acc = pl.accuracy(n=30)
    assert isinstance(acc, (int, float))
    assert 0.4 < acc < 0.6  # 1/2


def test_accuracy_by_substrategy_returns_dict(temp_predlog):
    pl = temp_predlog
    _add_prediction(pl, 1, "UP", "UP",     strategy="iron_condor", dte_bucket="45DTE", book="disciplined")
    _add_prediction(pl, 2, "UP", "DOWN",   strategy="iron_condor", dte_bucket="45DTE", book="disciplined")
    _add_prediction(pl, 3, "UP", "UP",     strategy="call_debit_spread", dte_bucket="0DTE", book="disciplined")
    acc = pl.accuracy(n=30, by_substrategy=True)
    assert isinstance(acc, dict)
    assert "iron_condor:45DTE:disciplined" in acc
    assert "call_debit_spread:0DTE:disciplined" in acc
    assert acc["iron_condor:45DTE:disciplined"] == pytest.approx(0.5, abs=0.01)
    assert acc["call_debit_spread:0DTE:disciplined"] == pytest.approx(1.0, abs=0.01)
    assert "all" in acc
    assert acc["all"] == pytest.approx(2/3, abs=0.01)


def test_accuracy_by_substrategy_excludes_unresolved(temp_predlog):
    pl = temp_predlog
    pl.add(date_str=date.today().isoformat(), direction="UP",
           strategy="iron_condor", dte_bucket="45DTE", book="disciplined")
    # Don't resolve it
    acc = pl.accuracy(n=30, by_substrategy=True)
    assert acc.get("iron_condor:45DTE:disciplined") is None or \
           "iron_condor:45DTE:disciplined" not in acc
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_predictions_per_substrategy.py -v
```
Expected: FAIL — `by_substrategy` arg doesn't exist; `Prediction.add` may not accept tags.

- [ ] **Step 4: Modify `learning/predictions.py`**

Implementer must:

1. Confirm the `Prediction` dataclass already has `strategy`, `dte_bucket`, `book` fields from Phase 2a. If yes, this task only extends `accuracy()`. If not, add them.

2. Extend the `accuracy()` method signature:

```python
    def accuracy(self, n: int = 30, by_substrategy: bool = False):
        """Return rolling accuracy over the last n resolved predictions.

        by_substrategy=False (default): returns a single float (aggregate),
                                        preserving backward compatibility.
        by_substrategy=True:  returns a dict keyed by "strategy:dte_bucket:book",
                              plus an "all" key for the aggregate. Sub-strategies
                              with fewer than 3 resolved samples are omitted.
        """
        resolved = [
            p for p in self._load_all_resolved()
            if p.get("actual") is not None
        ][-n:]
        if not resolved:
            return {} if by_substrategy else 0.0

        def _hits(rows):
            return sum(1 for r in rows if r.get("direction") == r.get("actual"))

        aggregate = _hits(resolved) / len(resolved)
        if not by_substrategy:
            return aggregate

        # Group by sub-strategy key
        groups: dict[str, list] = {}
        for r in resolved:
            s, d, b = r.get("strategy"), r.get("dte_bucket"), r.get("book")
            if not (s and d and b):
                continue
            key = f"{s}:{d}:{b}"
            groups.setdefault(key, []).append(r)

        out: dict[str, float] = {"all": aggregate}
        MIN_SAMPLES = 3
        for key, rows in groups.items():
            if len(rows) < MIN_SAMPLES:
                continue
            out[key] = _hits(rows) / len(rows)
        return out
```

Note: implementer adapts `_load_all_resolved` to the real method name in `predictions.py`.

- [ ] **Step 5: Update `learning/hypothesis_engine.py` to consume the dict**

Modify `propose_weekly` (around line 142) to request per-sub-strategy data:

```python
        ctx = {
            "date":             today_str,
            "rolling_accuracy": self.preds.accuracy(n=60, by_substrategy=True),
            # ... rest unchanged ...
        }
```

The Claude prompt already serializes the dict via `json.dumps(ctx['rolling_accuracy'])`, so no schema change to the prompt is needed.

- [ ] **Step 6: Update `learning/reflector.py` likewise**

Modify `_build_context` (around line 143-156):

```python
        accuracy  = self.preds.accuracy(n=30, by_substrategy=True)
```

- [ ] **Step 7: Run test to verify it passes**

```bash
pytest tests/test_predictions_per_substrategy.py -v
```
Expected: 3 PASS.

- [ ] **Step 8: Run full suite**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
```
Expected: 787 passing (784 + 3 new), 0 failures.

- [ ] **Step 9: Commit**

```bash
git add learning/predictions.py learning/hypothesis_engine.py learning/reflector.py tests/test_predictions_per_substrategy.py
git commit -m "feat(phase4a-2): rolling_accuracy per-sub-strategy

PredictionLog.accuracy() gains by_substrategy=True returning dict keyed
by strategy:dte_bucket:book with 'all' aggregate. MIN_SAMPLES=3 floor
prevents tiny-sample noise. hypothesis_engine + reflector both consume
the dict form.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 — Items 3+4: KB validator (confidence cap + evidence citation)

**Files:**
- Create: `learning/kb_validator.py`
- Modify: `learning/reflector.py`
- Modify: `config.py`
- Test:   `tests/test_kb_validator.py`

### Steps

- [ ] **Step 1: Write the failing test**

Create `tests/test_kb_validator.py`:

```python
"""Tests for kb_validator (Phase 4a items 3+4)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.kb_validator import (
    cap_daily_confidence,
    has_valid_evidence,
    validate_kb_entries,
)


# ── Item 3: confidence cap ──────────────────────────────────

def test_confidence_cap_applies_to_daily_entries_over_threshold():
    entry = {"category": "regime_accuracy", "confidence": 0.95}
    capped, was_capped = cap_daily_confidence(entry, kind="daily")
    assert capped["confidence"] == 0.7
    assert was_capped is True


def test_confidence_cap_skips_daily_entries_under_threshold():
    entry = {"category": "regime_accuracy", "confidence": 0.5}
    capped, was_capped = cap_daily_confidence(entry, kind="daily")
    assert capped["confidence"] == 0.5
    assert was_capped is False


def test_confidence_cap_skips_non_daily_kinds():
    entry = {"category": "hypothesis", "confidence": 0.95}
    capped, was_capped = cap_daily_confidence(entry, kind="hypothesis")
    assert capped["confidence"] == 0.95
    assert was_capped is False


def test_confidence_cap_skips_regime_drift():
    entry = {"category": "market_context", "confidence": 0.85}
    capped, was_capped = cap_daily_confidence(entry, kind="regime_drift")
    assert capped["confidence"] == 0.85
    assert was_capped is False


# ── Item 4: evidence citation ───────────────────────────────

def test_evidence_with_trade_id_passes():
    entry = {"evidence": "Trade AAAA0001 stopped out at $0.45"}
    trade_ids = {"AAAA0001"}
    today_numbers = {587.42}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_sim_trade_id_passes():
    entry = {"evidence": "sim_a3b2c1d4 hit profit target"}
    trade_ids = {"sim_a3b2c1d4"}
    today_numbers = set()
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_today_number_passes():
    entry = {"evidence": "SPY closed at 587.42 above MA200"}
    trade_ids = set()
    today_numbers = {587.42}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_close_float_match_within_tolerance():
    """±0.1% tolerance for float matches (per Q2 confirmation)."""
    entry = {"evidence": "SPY closed at 587.4"}  # vs today's 587.42
    trade_ids = set()
    today_numbers = {587.42}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True


def test_evidence_with_integer_exact_match_only():
    """Integers require exact match (per Q2)."""
    entry = {"evidence": "VIX above 15"}
    trade_ids = set()
    today_numbers = {15}
    assert has_valid_evidence(entry, trade_ids, today_numbers) is True

    entry2 = {"evidence": "VIX above 14"}  # 14 != 15 exact
    assert has_valid_evidence(entry2, trade_ids, today_numbers) is False


def test_evidence_pure_narrative_fails():
    entry = {"evidence": "Today was a choppy session with the market undecided"}
    trade_ids = set()
    today_numbers = set()
    assert has_valid_evidence(entry, trade_ids, today_numbers) is False


def test_evidence_empty_string_fails():
    entry = {"evidence": ""}
    assert has_valid_evidence(entry, set(), set()) is False


# ── Integration: validate_kb_entries ────────────────────────

def test_validate_caps_daily_and_logs_violations():
    parsed = {
        "kb_entries": [
            {"category": "regime_accuracy", "confidence": 0.92,
             "evidence": "trade AAAA0001 made $150"},
            {"category": "market_context", "confidence": 0.85,
             "evidence": "vague observation about the day"},
        ],
    }
    facts = {
        "trade_ids":      {"AAAA0001"},
        "today_numbers":  {150, 587.42},
    }
    out, metrics = validate_kb_entries(parsed, facts, default_kind="daily")
    assert out["kb_entries"][0]["confidence"] == 0.7  # capped
    assert out["kb_entries"][1]["confidence"] == 0.7  # also capped
    assert metrics["caps_applied"] == 2
    assert metrics["evidence_violations"] == 1
    # Violating entry gets marked but kept (soft enforcement)
    assert out["kb_entries"][1].get("evidence_violation") is True
    assert len(out["kb_entries"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_kb_validator.py -v
```
Expected: FAIL — `learning.kb_validator` module doesn't exist.

- [ ] **Step 3: Add Phase 4a constants to `config.py`**

Append at the end of `config.py`:

```python
# ─────────────────────────────────────────────────────────────
# Phase 4a — Learning Loop Hygiene
# ─────────────────────────────────────────────────────────────

# Item 3: KB confidence cap for single-day entries
KB_DAILY_CONFIDENCE_CAP = 0.7

# Item 4: evidence-citation tolerance for float matches (±0.1%)
KB_EVIDENCE_FLOAT_TOLERANCE_PCT = 0.1

# Item 5: anomaly triggers for reflector routing (Sonnet escalation)
REFLECTOR_ANOMALY_STOPS_MIN          = 2     # ≥N stop-outs today
REFLECTOR_ANOMALY_PRED_MISS_PCT      = 1.5   # |predicted - actual| as % of SPY
REFLECTOR_ANOMALY_NEW_SUBSTRATEGY    = True  # any sub-strategy fired 1st time
REFLECTOR_ANOMALY_REGIME_CHANGE      = True  # regime differs vs yesterday

# Item 6: regime-drift threshold for off_hours_learner
REGIME_DRIFT_THRESHOLD_PCT           = 10.0  # ≥N pts shift in 60d distribution
REGIME_DRIFT_RECENT_DAYS             = 60    # last-N trading days
REGIME_DRIFT_PRIOR_DAYS              = 60    # prior-N trading days for comparison
```

- [ ] **Step 4: Create `learning/kb_validator.py`**

```python
"""learning/kb_validator.py — Phase 4a items 3+4.

Pure-function validators applied after the reflector's Sonnet/phi4 reply
is parsed. Two checks:

  Item 3: single-day KB entries (kind="daily") have confidence capped at
          config.KB_DAILY_CONFIDENCE_CAP. Higher confidence reserved for
          multi-day-corroborated entries from hypothesis_engine and
          off_hours_learner.

  Item 4: each KB entry's `evidence` string must reference at least one
          concrete piece of evidence — a trade_id, a number from today's
          facts, or a previous KB entry slug. Pure narrative is flagged
          (soft enforcement — entry is kept and marked, not rejected).
"""
from __future__ import annotations

import re
import sys
import os
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


# 8-char uppercase hex (real TradeRecorder ids)
_TRADE_ID_RE = re.compile(r"\b[A-F0-9]{8}\b")
# sim_xxxxxxxx (simulated trade ids)
_SIM_ID_RE   = re.compile(r"\bsim_[0-9a-f]{8,16}\b")
# Numbers (int or float)
_NUMBER_RE   = re.compile(r"-?\d+\.?\d*")


def cap_daily_confidence(entry: dict, kind: str) -> tuple[dict, bool]:
    """Cap confidence at KB_DAILY_CONFIDENCE_CAP for kind=='daily'.

    Returns (entry, was_capped). Entry is mutated in place AND returned
    for chainability.
    """
    if kind != "daily":
        return entry, False
    cap = float(config.KB_DAILY_CONFIDENCE_CAP)
    conf = float(entry.get("confidence", 0.5))
    if conf > cap:
        entry["confidence"] = cap
        return entry, True
    return entry, False


def has_valid_evidence(entry: dict, trade_ids: set, today_numbers: set) -> bool:
    """Item 4: does the entry's `evidence` string reference concrete data?

    Returns True iff `evidence` contains:
      - a trade_id matching today's trades (real or sim_), OR
      - a number matching today's facts (±0.1% for floats, exact for ints), OR
      - a kb_<id> reference to a previous KB entry

    Pure narrative without specifics → False.
    """
    ev = entry.get("evidence") or ""
    if not isinstance(ev, str) or not ev.strip():
        return False

    # Trade id matches
    for m in _TRADE_ID_RE.findall(ev):
        if m in trade_ids:
            return True
    for m in _SIM_ID_RE.findall(ev):
        if m in trade_ids:
            return True

    # KB entry reference
    if re.search(r"\bkb_[a-z0-9_]+\b", ev):
        return True

    # Number matches
    tol_pct = float(config.KB_EVIDENCE_FLOAT_TOLERANCE_PCT) / 100.0
    for token in _NUMBER_RE.findall(ev):
        try:
            num = float(token)
        except ValueError:
            continue
        is_int = "." not in token
        for target in today_numbers:
            if is_int and isinstance(target, int):
                if int(num) == target:
                    return True
            else:
                # Float comparison with relative tolerance
                t = float(target)
                if t == 0:
                    if num == 0:
                        return True
                elif abs(num - t) / abs(t) <= tol_pct:
                    return True
    return False


def validate_kb_entries(parsed: dict, facts: dict,
                        default_kind: str = "daily") -> tuple[dict, dict]:
    """Apply both validators to parsed Sonnet/phi4 JSON.

    Args:
        parsed: dict with "kb_entries" list, as returned by reflector parser.
        facts: dict containing today's `trade_ids` set and `today_numbers` set.
        default_kind: kind to apply when entry doesn't specify (default 'daily').

    Returns (modified_parsed, metrics_dict). Entries are mutated in place.
    The metrics dict has keys: caps_applied, evidence_violations.
    """
    metrics = {"caps_applied": 0, "evidence_violations": 0}
    trade_ids     = facts.get("trade_ids", set())
    today_numbers = facts.get("today_numbers", set())

    for entry in parsed.get("kb_entries", []):
        kind = entry.get("kind", default_kind)
        _, was_capped = cap_daily_confidence(entry, kind)
        if was_capped:
            metrics["caps_applied"] += 1
            logger.info(
                f"kb_validator: capped confidence on '{entry.get('claim','')[:60]}'"
            )
        if not has_valid_evidence(entry, trade_ids, today_numbers):
            metrics["evidence_violations"] += 1
            entry["evidence_violation"] = True
            logger.warning(
                f"kb_validator: evidence-citation violation on '{entry.get('claim','')[:60]}'"
            )
    parsed["_validator_metrics"] = metrics
    return parsed, metrics
```

- [ ] **Step 5: Wire validator into `learning/reflector.py`**

Modify `reflect_today` (around line 91-118). After `parsed, parse_err = self._parse_reply(reply)` and BEFORE the loop that appends KB entries:

```python
        # Phase 4a items 3+4: validate KB entries
        if parsed:
            from learning.kb_validator import validate_kb_entries
            today_numbers = self._extract_today_numbers(context)
            today_trade_ids = self._extract_today_trade_ids(context)
            parsed, _ = validate_kb_entries(
                parsed,
                facts={"trade_ids": today_trade_ids,
                       "today_numbers": today_numbers},
                default_kind="daily",
            )
```

Then add two helper methods to `Reflector`:

```python
    @staticmethod
    def _extract_today_numbers(ctx: dict) -> set:
        """Pull all numeric facts from today's context for evidence-check."""
        nums: set = set()
        pred = ctx.get("prediction") or {}
        # Common numeric fields the reflector sees
        for k in ("predicted_close", "actual_close", "vix", "adx",
                  "ma200_dist", "spy_close", "score"):
            v = pred.get(k)
            if isinstance(v, (int, float)):
                nums.add(v)
        for pos in ctx.get("open_positions", []):
            for k in ("entry_price", "exit_price", "pnl_dollars", "pnl_pct"):
                v = pos.get(k)
                if isinstance(v, (int, float)):
                    nums.add(v)
        return nums

    @staticmethod
    def _extract_today_trade_ids(ctx: dict) -> set:
        """Pull trade_ids from today's open positions (real or simulated)."""
        return {pos.get("trade_id") for pos in ctx.get("open_positions", [])
                if pos.get("trade_id")}
```

- [ ] **Step 6: Run validator tests to verify pass**

```bash
pytest tests/test_kb_validator.py -v
```
Expected: 11 PASS.

- [ ] **Step 7: Run full suite**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
```
Expected: 798 passing (787 + 11 new), 0 failures.

- [ ] **Step 8: Commit**

```bash
git add learning/kb_validator.py learning/reflector.py config.py tests/test_kb_validator.py
git commit -m "feat(phase4a-3+4): KB validator — confidence cap + evidence citation

Items 3+4 ship together as one validator pass run after reflector's
Sonnet/phi4 reply is parsed. Confidence on daily entries capped at 0.7.
Evidence-citation violations logged + entry marked but kept (soft
enforcement). ±0.1% tolerance for float matches; exact for integers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6 — Item 5: Anomaly-routed reflector

**Files:**
- Create: `learning/anomaly_detector.py`
- Modify: `learning/reflector.py`
- Test:   `tests/test_anomaly_detector.py`
- Test:   `tests/test_reflector_routing.py`

### Steps

- [ ] **Step 1: Write the anomaly-detector test**

Create `tests/test_anomaly_detector.py`:

```python
"""Tests for anomaly_detector (Phase 4a item 5)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.anomaly_detector import is_anomalous_day


def test_normal_day_not_anomalous():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.3,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is False


def test_two_stops_triggers_anomaly():
    facts = {
        "stops_today": 2,
        "prediction_miss_pct": 0.5,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_one_stop_below_threshold_not_anomalous(monkeypatch):
    monkeypatch.setattr(config, "REFLECTOR_ANOMALY_STOPS_MIN", 2)
    facts = {
        "stops_today": 1,
        "prediction_miss_pct": 0.5,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is False


def test_large_prediction_miss_triggers_anomaly():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 2.0,  # > default 1.5
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_negative_prediction_miss_uses_abs_value():
    """Q3 confirmed: absolute magnitude delta."""
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": -2.5,
        "new_substrategies_today": [],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_new_substrategy_triggers_anomaly():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.0,
        "new_substrategies_today": ["iron_condor_0DTE"],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is True


def test_regime_change_triggers_anomaly():
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.0,
        "new_substrategies_today": [],
        "regime_changed_today": True,
    }
    assert is_anomalous_day(facts) is True


def test_disabled_new_substrategy_flag_does_not_trigger(monkeypatch):
    monkeypatch.setattr(config, "REFLECTOR_ANOMALY_NEW_SUBSTRATEGY", False)
    facts = {
        "stops_today": 0,
        "prediction_miss_pct": 0.0,
        "new_substrategies_today": ["iron_condor_0DTE"],
        "regime_changed_today": False,
    }
    assert is_anomalous_day(facts) is False


def test_missing_fields_default_safe():
    """Empty facts should not crash; default to not anomalous."""
    facts = {}
    assert is_anomalous_day(facts) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_anomaly_detector.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `learning/anomaly_detector.py`**

```python
"""learning/anomaly_detector.py — Phase 4a item 5.

Pure function: given today's facts, decide whether the reflector should
escalate to Sonnet (anomaly) or stay on phi4 (normal day).

Thresholds are config constants — tune without code changes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


def is_anomalous_day(facts: dict) -> bool:
    """Return True if today warrants Sonnet's reasoning depth.

    Triggers (any single trigger fires = anomaly):
        - stops_today >= REFLECTOR_ANOMALY_STOPS_MIN
        - |prediction_miss_pct| > REFLECTOR_ANOMALY_PRED_MISS_PCT   (absolute magnitude delta — Q3)
        - REFLECTOR_ANOMALY_NEW_SUBSTRATEGY enabled AND any sub-strategy fired first time
        - REFLECTOR_ANOMALY_REGIME_CHANGE enabled AND regime differs from yesterday

    Missing fact fields default to safe (not anomalous) — facts.get() with
    sensible defaults.
    """
    if facts.get("stops_today", 0) >= config.REFLECTOR_ANOMALY_STOPS_MIN:
        return True

    pred_miss = facts.get("prediction_miss_pct", 0.0)
    if abs(float(pred_miss)) > float(config.REFLECTOR_ANOMALY_PRED_MISS_PCT):
        return True

    if config.REFLECTOR_ANOMALY_NEW_SUBSTRATEGY and facts.get("new_substrategies_today"):
        return True

    if config.REFLECTOR_ANOMALY_REGIME_CHANGE and facts.get("regime_changed_today"):
        return True

    return False
```

- [ ] **Step 4: Run anomaly_detector test to verify pass**

```bash
pytest tests/test_anomaly_detector.py -v
```
Expected: 9 PASS.

- [ ] **Step 5: Write the reflector routing test**

Create `tests/test_reflector_routing.py`:

```python
"""Tests for reflector routing (Phase 4a item 5)."""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.reflector import Reflector


@pytest.fixture
def isolated_reflector(monkeypatch, tmp_path):
    """A Reflector with mock deps and a temp LOG_DIR."""
    monkeypatch.setattr("config.LOG_DIR", str(tmp_path))
    kb     = MagicMock()
    preds  = MagicMock()
    plans  = MagicMock()
    trades = MagicMock()
    preds.get.return_value      = {"direction": "UP"}
    preds.accuracy.return_value = {"all": 0.55}
    plans.get_plan.return_value = {}
    kb.recent.return_value      = []
    trades.get_all_trades.return_value = []
    yield Reflector(
        knowledge_base=kb, prediction_log=preds,
        plan_logger=plans, trade_recorder=trades,
        api_key="fake_key",
    )


def test_normal_day_routes_to_phi4(isolated_reflector, monkeypatch):
    """When _is_anomalous_day returns False, call_llm receives model_preference='phi4_first'."""
    monkeypatch.setattr(
        "learning.reflector.is_anomalous_day", lambda facts: False
    )
    with patch("learning.reflector.call_llm",
               return_value='{"summary":"ok","narrative":"-","kb_entries":[]}') as cm:
        isolated_reflector.reflect_today(today=date(2026, 5, 27))
        assert cm.called
        kwargs = cm.call_args.kwargs
        assert kwargs.get("model_preference") == "phi4_first"


def test_anomalous_day_routes_to_sonnet(isolated_reflector, monkeypatch):
    """When _is_anomalous_day returns True, call_llm omits phi4_first preference."""
    monkeypatch.setattr(
        "learning.reflector.is_anomalous_day", lambda facts: True
    )
    with patch("learning.reflector.call_llm",
               return_value='{"summary":"ok","narrative":"-","kb_entries":[]}') as cm:
        isolated_reflector.reflect_today(today=date(2026, 5, 27))
        kwargs = cm.call_args.kwargs
        assert kwargs.get("model_preference") in (None, "sonnet_first")


def test_routing_recorded_in_result(isolated_reflector, monkeypatch):
    """The result dict must include the _route used (telemetry)."""
    monkeypatch.setattr(
        "learning.reflector.is_anomalous_day", lambda facts: True
    )
    with patch("learning.reflector.call_llm",
               return_value='{"summary":"ok","narrative":"-","kb_entries":[]}'):
        result = isolated_reflector.reflect_today(today=date(2026, 5, 27))
        assert result.get("route") in ("sonnet_anomaly", "sonnet_fallback")
```

- [ ] **Step 6: Run routing test to verify it fails**

```bash
pytest tests/test_reflector_routing.py -v
```
Expected: FAIL — routing not yet wired.

- [ ] **Step 7: Wire routing into `learning/reflector.py`**

At the top of the file, add imports:

```python
from data.llm_client            import call_llm
from learning.anomaly_detector  import is_anomalous_day
```

Replace `_call_claude` (current body around line 172-182) with:

```python
    def _call_claude(self, prompt: str, facts: dict) -> tuple[str, str]:
        """Route based on anomaly detection. Returns (reply_text, route_label)."""
        anomalous = is_anomalous_day(facts)
        if anomalous:
            logger.info("Reflector: anomalous day → Sonnet")
            try:
                text = call_llm(
                    system               = REFLECTOR_SYSTEM,
                    user                 = prompt,
                    anthropic_model      = CLAUDE_MODEL,
                    api_key              = self.api_key,
                    max_tokens           = 1500,
                    cache_static_system  = True,
                    model_preference     = "sonnet_first",
                )
                return text, "sonnet_anomaly"
            except Exception as e:
                logger.error(f"Sonnet anomaly call failed: {e}")
                return "", "sonnet_anomaly_error"
        else:
            logger.info("Reflector: normal day → phi4")
            text = call_llm(
                system               = REFLECTOR_SYSTEM,
                user                 = prompt,
                anthropic_model      = CLAUDE_MODEL,
                api_key              = self.api_key,
                max_tokens           = 1500,
                cache_static_system  = True,
                model_preference     = "phi4_first",
            )
            # call_llm already escalates phi4→Sonnet on failure
            return text, "phi4"
```

Modify `reflect_today` (around line 91-135) to gather anomaly facts and pass them:

```python
    def reflect_today(self, today: date | None = None) -> dict:
        today     = today or date.today()
        today_str = today.isoformat()
        context   = self._build_context(today_str)
        prompt    = self._build_prompt(context)

        # Phase 4a item 5: gather anomaly facts and route
        facts = self._gather_anomaly_facts(context)
        reply, route = self._call_claude(prompt, facts)
        parsed, parse_err = self._parse_reply(reply)

        # Phase 4a items 3+4: validate KB entries (kept from Task 5)
        if parsed:
            from learning.kb_validator import validate_kb_entries
            today_numbers = self._extract_today_numbers(context)
            today_trade_ids = self._extract_today_trade_ids(context)
            parsed, _ = validate_kb_entries(
                parsed,
                facts={"trade_ids": today_trade_ids,
                       "today_numbers": today_numbers},
                default_kind="daily",
            )

        md_path = self._save_markdown(today_str, parsed, reply, context, parse_err)

        kb_ids: list[str] = []
        if parsed and parsed.get("kb_entries"):
            for raw in parsed["kb_entries"]:
                try:
                    entry = KBEntry(
                        date       = today_str,
                        category   = raw.get("category", "other"),
                        claim      = raw.get("claim", "")[:500],
                        evidence   = raw.get("evidence", "")[:1000],
                        confidence = float(raw.get("confidence", 0.5)),
                        source     = "reflector",
                        tags       = list(raw.get("tags") or [])[:8],
                    )
                    kb_ids.append(self.kb.append(entry))
                except Exception as e:
                    logger.warning(f"Reflector: skipping malformed KB entry: {e}")

        # post_fn block unchanged ...
        if self.post and parsed and parsed.get("summary"):
            try:
                self.post(
                    f"🪞 **Daily Reflection {today_str}**\n"
                    f"{parsed['summary']}\n"
                    f"_+{len(kb_ids)} KB entries (route: {route}) -- see {md_path}_"
                )
            except Exception as e:
                logger.warning(f"Reflector: post_fn failed: {e}")

        return {
            "date":       today_str,
            "markdown":   md_path,
            "kb_ids":     kb_ids,
            "parsed":     bool(parsed),
            "parse_err":  parse_err,
            "route":      route,           # Phase 4a item 5 telemetry
        }
```

Add the `_gather_anomaly_facts` method:

```python
    def _gather_anomaly_facts(self, ctx: dict) -> dict:
        """Build the facts dict the anomaly detector inspects.

        Note: 'new_substrategies_today' tracks substrategies that fired
        for the first time in history. 'regime_changed_today' compares
        today's predicted regime against yesterday's prediction.
        """
        pred = ctx.get("prediction") or {}
        today_pred = float(pred.get("predicted_move_pct", 0) or 0)
        today_actual = float(pred.get("actual_move_pct", 0) or 0)
        miss_pct = today_actual - today_pred

        # Stops today (from open_positions or recent closed trades)
        stops = 0
        for pos in ctx.get("open_positions", []) or []:
            if pos.get("exit_reason") == "stop":
                stops += 1

        # New sub-strategies fired today: list of strategy:dte_bucket strings
        new_subs: list[str] = []
        try:
            seen_subs = self._historical_substrategies()
            for pos in ctx.get("open_positions", []) or []:
                key = f"{pos.get('strategy')}:{pos.get('dte_bucket')}"
                if key not in seen_subs:
                    new_subs.append(key)
        except Exception:
            pass

        # Regime change vs yesterday
        regime_changed = self._regime_changed_vs_yesterday(pred)

        return {
            "stops_today":              stops,
            "prediction_miss_pct":      miss_pct,
            "new_substrategies_today":  new_subs,
            "regime_changed_today":     regime_changed,
        }

    def _historical_substrategies(self) -> set[str]:
        """Set of 'strategy:dte_bucket' strings that have fired in history.

        Reads from TradeRecorder (real + simulated for fairness — a
        sub-strategy that backfilled is not 'new' in the data sense).
        """
        all_trades = self.trades.get_trades_by(include_simulated=True)
        return {
            f"{t.get('strategy')}:{t.get('dte_bucket')}"
            for t in all_trades
            if t.get('strategy') and t.get('dte_bucket')
        }

    def _regime_changed_vs_yesterday(self, today_pred: dict) -> bool:
        """Compare today's regime classification to the prior weekday's."""
        today_regime = today_pred.get("regime")
        if not today_regime:
            return False
        try:
            from datetime import date, timedelta
            from learning.predictions import PredictionLog
            # Walk back up to 4 days to skip weekends
            for delta in range(1, 5):
                prior_str = (date.today() - timedelta(days=delta)).isoformat()
                prior = self.preds.get(prior_str)
                if prior:
                    return prior.get("regime") != today_regime
        except Exception:
            pass
        return False
```

- [ ] **Step 8: Run routing tests to verify pass**

```bash
pytest tests/test_reflector_routing.py tests/test_anomaly_detector.py -v
```
Expected: 12 PASS (9 anomaly + 3 routing).

- [ ] **Step 9: Run full suite**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
```
Expected: 810 passing (798 + 12 new), 0 failures.

- [ ] **Step 10: Commit**

```bash
git add learning/anomaly_detector.py learning/reflector.py tests/test_anomaly_detector.py tests/test_reflector_routing.py
git commit -m "feat(phase4a-5): local-first reflector with anomaly-hybrid routing

phi4 (Ollama) handles normal-day reflection. Sonnet escalates on
anomaly triggers: ≥2 stops, |pred miss| > 1.5%, new sub-strategy,
regime change vs yesterday. Routing recorded as result.route for
weekly telemetry. Triggers are config constants — tunable without
code changes. First-Tuesday expected escalation: 80-100% as
substrategies fire for first time; steady-state target 20-40%.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7 — Item 6: Off-hours regime-drift pivot

**Files:**
- Modify: `learning/off_hours_learner.py` (significant rewrite)
- Test:   `tests/test_off_hours_regime_drift.py`

### Steps

- [ ] **Step 1: Write the failing test**

Create `tests/test_off_hours_regime_drift.py`:

```python
"""Tests for off-hours regime-drift detection (Phase 4a item 6)."""
import os
import sys
import tempfile
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.off_hours_learner import (
    OffHoursLearner,
    compute_distribution,
    detect_shifts,
    compute_feature_trends,
)


def test_compute_distribution_pct_sums_to_100():
    rows = [
        {"regime": "TRENDING_UP_CALM"},
        {"regime": "TRENDING_UP_CALM"},
        {"regime": "CHOPPY_LOW_VOL"},
        {"regime": "CHOPPY_LOW_VOL"},
    ]
    dist = compute_distribution(rows)
    assert dist["TRENDING_UP_CALM"] == pytest.approx(50.0)
    assert dist["CHOPPY_LOW_VOL"]   == pytest.approx(50.0)
    assert sum(dist.values()) == pytest.approx(100.0)


def test_compute_distribution_empty():
    assert compute_distribution([]) == {}


def test_detect_shifts_above_threshold():
    prior  = {"A": 50.0, "B": 30.0, "C": 20.0}
    recent = {"A": 30.0, "B": 30.0, "C": 40.0}
    shifts = detect_shifts(prior, recent, threshold_pct=10.0)
    keys = {s["regime"] for s in shifts}
    assert "A" in keys  # -20
    assert "C" in keys  # +20
    assert "B" not in keys  # 0


def test_detect_shifts_below_threshold_empty():
    prior  = {"A": 50.0, "B": 50.0}
    recent = {"A": 55.0, "B": 45.0}
    shifts = detect_shifts(prior, recent, threshold_pct=10.0)
    assert shifts == []


def test_compute_feature_trends_returns_means():
    rows = [
        {"vix": 14.0, "adx": 22.0, "ma200_dist": 3.0},
        {"vix": 15.0, "adx": 24.0, "ma200_dist": 3.5},
        {"vix": 16.0, "adx": 26.0, "ma200_dist": 4.0},
    ]
    trends = compute_feature_trends(rows)
    assert trends["vix_mean"]   == pytest.approx(15.0)
    assert trends["adx_mean"]   == pytest.approx(24.0)
    assert trends["ma200_dist_mean"] == pytest.approx(3.5)


def test_run_writes_report_when_classifications_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    kb = MagicMock()
    kb.append = MagicMock(return_value="kb_xx01")

    # Build 130 days of fake classifications (prior 60 + recent 60 + buffer)
    base = date.today() - timedelta(days=170)
    fake_rows = []
    for i in range(130):
        d = base + timedelta(days=i)
        # First 65 days: lots of TRENDING_UP_CALM; last 65 days: lots of RANGE_HIGH_VOL
        regime = "TRENDING_UP_CALM" if i < 65 else "RANGE_HIGH_VOL"
        fake_rows.append({"date": d, "regime": regime, "vix": 14.0, "adx": 22.0,
                           "ma200_dist": 3.0})

    learner = OffHoursLearner(knowledge_base=kb, api_key="fake_key")
    with patch.object(learner, "_load_regime_classifications", return_value=fake_rows), \
         patch("learning.off_hours_learner.call_llm",
               return_value='{"kb_entries":[{"category":"market_context","claim":"shift detected","evidence":"TRENDING_UP_CALM dropped 30%","confidence":0.75}]}'):
        result = learner.run(today=date.today())
    assert "shift_count" in result
    assert result["shift_count"] >= 1   # at least TRENDING_UP_CALM or RANGE_HIGH_VOL crosses threshold
    assert kb.append.called


def test_run_with_no_shifts_still_calls_claude(monkeypatch, tmp_path):
    """Per spec: silent regimes are info too — Sonnet still produces an entry."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    kb = MagicMock()
    kb.append = MagicMock(return_value="kb_xx02")
    fake_rows = []
    for i in range(130):
        d = date.today() - timedelta(days=170 - i)
        fake_rows.append({"date": d, "regime": "TRENDING_UP_CALM",
                          "vix": 14.0, "adx": 22.0, "ma200_dist": 3.0})

    learner = OffHoursLearner(knowledge_base=kb, api_key="fake_key")
    with patch.object(learner, "_load_regime_classifications", return_value=fake_rows), \
         patch("learning.off_hours_learner.call_llm",
               return_value='{"kb_entries":[{"category":"market_context","claim":"stable","evidence":"no shifts","confidence":0.6}]}') as cm:
        result = learner.run(today=date.today())
    assert cm.called
    assert result["shift_count"] == 0


def test_run_insufficient_history_skips_call(monkeypatch, tmp_path):
    """If <60 trading days available, skip the Claude call."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    kb = MagicMock()
    fake_rows = [
        {"date": date.today() - timedelta(days=i), "regime": "TRENDING_UP_CALM",
         "vix": 14.0, "adx": 22.0, "ma200_dist": 3.0}
        for i in range(40)
    ]
    learner = OffHoursLearner(knowledge_base=kb, api_key="fake_key")
    with patch.object(learner, "_load_regime_classifications", return_value=fake_rows), \
         patch("learning.off_hours_learner.call_llm") as cm:
        result = learner.run(today=date.today())
    assert result.get("skipped") is True
    assert not cm.called
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_off_hours_regime_drift.py -v
```
Expected: FAIL — new helpers don't exist; OffHoursLearner doesn't do drift.

- [ ] **Step 3: Rewrite `learning/off_hours_learner.py`**

Replace the entire file contents with:

```python
"""learning/off_hours_learner.py — Phase 4a item 6.

Weekend learning. Pivot from "near-miss threshold tuning" → "regime-drift
detection". Runs Sunday 10:00 ET.

Pivot rationale: with Phase 3 paper trading live, near-miss tuning is
redundant with hypothesis_engine (Saturday). Regime drift catches the
highest-leverage signal — meta-shifts in market structure that affect
ALL sub-strategies at once — and has always-available data regardless
of trade volume.

Algorithm:
  1. Load 120 trading days of SPY regime classifications.
  2. Split into recent-60d vs prior-60d windows.
  3. Compute regime distribution (% of days per regime) for each window.
  4. Identify shifts ≥REGIME_DRIFT_THRESHOLD_PCT (default 10pts).
  5. Compute feature trends (VIX/ADX/MA200_dist means) for narrative.
  6. Send shifts + trends to Sonnet; persist KB entry as kind=regime_drift.
  7. If <60 trading days available, skip the Claude call.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning.knowledge_base import KnowledgeBase, KBEntry
from data.llm_client          import call_llm


CLAUDE_MODEL = "claude-sonnet-4-6"

LEARNER_SYSTEM = """You are the trading assistant's off-hours regime-drift module.

You receive:
  - A list of regime distribution SHIFTS between a recent-60d window and a
    prior-60d window (each shift = {regime, recent_pct, prior_pct, delta_pct}).
  - Feature trends (VIX/ADX/MA200_dist means for each window).

Your job: interpret the shifts in terms of trading implications. Are we
moving toward more trending regimes? More volatility? Which strategies'
typical conditions are becoming more/less common?

Return ONLY a single JSON object:

{
  "kb_entries": [
    {
      "category":   "market_context",
      "kind":       "regime_drift",
      "claim":      "<= 200 chars",
      "evidence":   "specific deltas and feature trends",
      "confidence": 0.0 to 1.0,
      "tags":       ["regime_drift", ...]
    }
  ]
}"""


# ────────────────────────────────────────────────────────────
#  Pure helpers (importable for tests + reuse)
# ────────────────────────────────────────────────────────────

def compute_distribution(rows: list[dict]) -> dict[str, float]:
    """Return {regime: pct} from a list of {regime: ...} rows."""
    if not rows:
        return {}
    counts: dict[str, int] = {}
    for r in rows:
        rg = r.get("regime")
        if not rg:
            continue
        counts[rg] = counts.get(rg, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: round(v / total * 100, 2) for k, v in counts.items()}


def detect_shifts(prior: dict[str, float], recent: dict[str, float],
                  threshold_pct: float) -> list[dict]:
    """Return list of regime shifts whose abs(delta_pct) >= threshold_pct.

    Each shift: {regime, recent_pct, prior_pct, delta_pct}.
    """
    all_regimes = set(prior) | set(recent)
    shifts = []
    for rg in all_regimes:
        p = prior.get(rg, 0.0)
        r = recent.get(rg, 0.0)
        delta = r - p
        if abs(delta) >= threshold_pct:
            shifts.append({
                "regime":     rg,
                "recent_pct": r,
                "prior_pct":  p,
                "delta_pct":  round(delta, 1),
            })
    return shifts


def compute_feature_trends(rows: list[dict]) -> dict[str, float]:
    """Compute mean VIX/ADX/MA200_dist over the rows."""
    if not rows:
        return {}

    def mean(field):
        vals = [r.get(field) for r in rows if isinstance(r.get(field), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "vix_mean":          mean("vix"),
        "adx_mean":          mean("adx"),
        "ma200_dist_mean":   mean("ma200_dist"),
    }


# ────────────────────────────────────────────────────────────
#  Learner
# ────────────────────────────────────────────────────────────

class OffHoursLearner:
    """Weekend regime-drift detector + Claude pattern-finding."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        api_key:        str | None    = None,
    ):
        self.kb      = knowledge_base or KnowledgeBase()
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    # ── MAIN ──────────────────────────────────────────

    def run(self, today: date | None = None) -> dict:
        today     = today or date.today()
        today_str = today.isoformat()

        rows = self._load_regime_classifications(today)
        recent_days = int(config.REGIME_DRIFT_RECENT_DAYS)
        prior_days  = int(config.REGIME_DRIFT_PRIOR_DAYS)
        need_total  = recent_days + prior_days

        if len(rows) < need_total:
            logger.info(
                f"OffHoursLearner: insufficient history "
                f"({len(rows)} < {need_total}) — skipping"
            )
            return {"date": today_str, "skipped": True, "rows_available": len(rows)}

        rows = sorted(rows, key=lambda r: r["date"])[-need_total:]
        prior  = rows[:prior_days]
        recent = rows[prior_days:]

        prior_dist  = compute_distribution(prior)
        recent_dist = compute_distribution(recent)
        shifts = detect_shifts(prior_dist, recent_dist,
                               threshold_pct=float(config.REGIME_DRIFT_THRESHOLD_PCT))
        trends_prior  = compute_feature_trends(prior)
        trends_recent = compute_feature_trends(recent)

        report = {
            "date":         today_str,
            "window_recent": f"{recent[0]['date']} to {recent[-1]['date']}",
            "window_prior":  f"{prior[0]['date']} to {prior[-1]['date']}",
            "prior_dist":    prior_dist,
            "recent_dist":   recent_dist,
            "shifts":        shifts,
            "feature_trends": {
                "prior":  trends_prior,
                "recent": trends_recent,
            },
            "shift_count":   len(shifts),
        }

        report_path = os.path.join(
            config.LOG_DIR, "learning", "off_hours", f"{today_str}.json"
        )
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        kb_ids = self._ask_claude_for_interpretation(today_str, report)
        return {
            "date":        today_str,
            "shift_count": len(shifts),
            "kb_appended": len(kb_ids),
            "kb_ids":      kb_ids,
            "report_path": report_path,
        }

    # ── REGIME CLASSIFICATION LOAD ────────────────────

    def _load_regime_classifications(self, today: date) -> list[dict]:
        """Load ~170 calendar days of SPY rows and classify each.

        Returns list of dicts {date, regime, vix, adx, ma200_dist} sorted by date.
        """
        try:
            import pandas as pd
            from signals.regime_detector import RegimeDetector
        except Exception as e:
            logger.warning(f"OffHoursLearner: deps missing -- {e}")
            return []

        csv_path = os.path.join("backtests", "spy_history.csv")
        if not os.path.exists(csv_path):
            logger.warning("OffHoursLearner: backtests/spy_history.csv missing")
            return []
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        df.index   = pd.to_datetime(df.index).date
        df = df.sort_index()

        # Look back 170 calendar days to get ~120 trading days
        cutoff = today - timedelta(days=170)
        df = df[df.index >= cutoff]
        if len(df) < 60:
            return []

        # Load VIX history (best-effort)
        vix_lookup: dict = {}
        try:
            from data.vix_client import VIXClient
            vdf = VIXClient().get_history(days=180)
            if vdf is not None and len(vdf) > 0:
                vix_lookup = {d: float(c) for d, c in vdf["close"].items()}
        except Exception:
            pass

        detector = RegimeDetector()
        rows: list[dict] = []
        dates = sorted(df.index)
        for i, d in enumerate(dates):
            if i < 210:    # need lookback for indicators
                continue
            hist = df.loc[dates[max(0, i-250):i]].copy()
            hist.index = pd.to_datetime(hist.index)
            vix_today = vix_lookup.get(d, 16.0)
            try:
                r = detector.classify(
                    spy_daily_df=hist,
                    vix_current=vix_today,
                    ivr_current=30.0,
                    today=d,
                )
            except Exception:
                continue
            rows.append({
                "date":       d,
                "regime":     r.regime.value,
                "vix":        vix_today,
                "adx":        r.metrics.get("adx", 0.0),
                "ma200_dist": r.metrics.get("ma200_dist_%", 0.0),
            })
        return rows

    # ── CLAUDE ────────────────────────────────────────

    def _ask_claude_for_interpretation(
        self, today_str: str, report: dict
    ) -> list[str]:
        if not self.api_key:
            logger.info("OffHoursLearner: no API key -- skipping Claude pass")
            return []
        try:
            text = call_llm(
                system               = LEARNER_SYSTEM,
                user                 = (
                    f"WINDOWS: {report['window_prior']} (prior) vs "
                    f"{report['window_recent']} (recent)\n\n"
                    f"PRIOR DISTRIBUTION:\n{json.dumps(report['prior_dist'], indent=2)}\n\n"
                    f"RECENT DISTRIBUTION:\n{json.dumps(report['recent_dist'], indent=2)}\n\n"
                    f"SHIFTS (|delta| >= {config.REGIME_DRIFT_THRESHOLD_PCT}%):\n"
                    f"{json.dumps(report['shifts'], indent=2)}\n\n"
                    f"FEATURE TRENDS:\n{json.dumps(report['feature_trends'], indent=2)}\n\n"
                    f"Produce JSON now."
                ),
                anthropic_model      = CLAUDE_MODEL,
                api_key              = self.api_key,
                max_tokens           = 1000,
                cache_static_system  = True,
            )
        except Exception as e:
            logger.error(f"OffHoursLearner Claude failed: {e}")
            return []

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

        ids = []
        for raw in parsed.get("kb_entries", []):
            try:
                ids.append(self.kb.append(KBEntry(
                    date       = today_str,
                    category   = raw.get("category", "market_context"),
                    claim      = raw.get("claim", "")[:500],
                    evidence   = raw.get("evidence", "")[:1000],
                    confidence = float(raw.get("confidence", 0.65)),
                    source     = "off_hours_learner",
                    tags       = list(raw.get("tags") or [])[:8] + ["regime_drift"],
                )))
            except Exception as e:
                logger.warning(f"OffHoursLearner: bad entry skipped -- {e}")
        return ids
```

- [ ] **Step 4: Run regime-drift tests to verify pass**

```bash
pytest tests/test_off_hours_regime_drift.py -v
```
Expected: 8 PASS.

- [ ] **Step 5: Confirm the old near-miss tests (if any) are updated or removed**

```bash
grep -r "near_miss\|near-miss" tests/ --include='*.py' -l
```

If any test files reference the removed near-miss API, the implementer subagent must update or delete them. Likely candidates: `tests/test_off_hours_learner.py` (if it exists). For each old test:
- If it tests the public `run()` interface only → update to new behavior
- If it tests near-miss internals → delete (those internals are gone)

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
```
Expected: 818 passing (810 + 8 new), 0 failures. If near-miss tests existed and were updated/deleted, the count may differ — that's fine as long as no failures.

- [ ] **Step 7: Commit**

```bash
git add learning/off_hours_learner.py tests/test_off_hours_regime_drift.py
git commit -m "feat(phase4a-6): off-hours learner pivot to regime-drift detection

Replaces near-miss threshold tuning (redundant with hypothesis_engine)
with 60v60 regime distribution comparison + feature trends. Catches
meta-shifts in market structure that affect all sub-strategies. KB
entries tagged kind=regime_drift, confidence not capped by validator.
Walk-forward boundary: observation only, no parameter selection.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8 — Integration smoke test + final BUILD_LOG update

**Files:**
- Modify: `BUILD_LOG.md`
- (No new code)

### Steps

- [ ] **Step 1: Run the full test suite one final time**

```bash
pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -30
```
Expected: ~818 passing, 0 failures. Save the count to mention in BUILD_LOG.

- [ ] **Step 2: Smoke-test reflector with a fake "anomalous" day**

```bash
python -c "
import os
os.environ.setdefault('ANTHROPIC_API_KEY', 'sk-test-FAKE')
from learning.anomaly_detector import is_anomalous_day
print('normal day:    ', is_anomalous_day({'stops_today': 0, 'prediction_miss_pct': 0.3, 'new_substrategies_today': [], 'regime_changed_today': False}))
print('2-stop day:    ', is_anomalous_day({'stops_today': 2, 'prediction_miss_pct': 0.3, 'new_substrategies_today': [], 'regime_changed_today': False}))
print('big miss day:  ', is_anomalous_day({'stops_today': 0, 'prediction_miss_pct': 2.5, 'new_substrategies_today': [], 'regime_changed_today': False}))
print('new sub day:   ', is_anomalous_day({'stops_today': 0, 'prediction_miss_pct': 0.0, 'new_substrategies_today': ['iron_condor_0DTE'], 'regime_changed_today': False}))
"
```
Expected:
```
normal day:     False
2-stop day:     True
big miss day:   True
new sub day:    True
```

- [ ] **Step 3: Smoke-test that `get_trades_by(include_simulated=True)` returns a non-empty list (assumes Task 2 already seeded)**

```bash
python -c "
from journal.trade_recorder import TradeRecorder
tr = TradeRecorder()
real = tr.get_trades_by(strategy='iron_condor', dte_bucket='45DTE')
unioned = tr.get_trades_by(strategy='iron_condor', dte_bucket='45DTE', include_simulated=True)
print(f'real-only iron_condor 45DTE: {len(real)}')
print(f'unioned:                    {len(unioned)}')
assert len(unioned) >= len(real), 'simulated trades not unioning'
print('OK — simulated trades are unioned in.')
"
```

- [ ] **Step 4: Smoke-test the off-hours learner end-to-end (without making a real Claude call)**

```bash
python -c "
from unittest.mock import patch
from learning.off_hours_learner import OffHoursLearner
from datetime import date
with patch('learning.off_hours_learner.call_llm', return_value='{\"kb_entries\":[]}'):
    l = OffHoursLearner(api_key=None)
    result = l.run(today=date.today())
print('result keys:', sorted(result))
print('result:', result)
"
```
Expected: result has either `skipped: True` (if backtest CSV doesn't have enough history) OR shift detection results. Either is acceptable here — we only need the code path to not crash.

- [ ] **Step 5: Update `BUILD_LOG.md` with Phase 4a entry**

Read the existing BUILD_LOG.md, append a new entry at the top of the entries section (above the most recent Phase 3 entry):

```markdown
## 2026-05-23 — Phase 4a: learning-loop hygiene

**Spec:** `docs/superpowers/specs/2026-05-23-phase4a-learning-loop-hygiene-design.md`
**Plan:** `docs/superpowers/plans/2026-05-23-phase4a-learning-loop-hygiene.md`

Shipped 7 hygiene items across 8 tasks:

  0a. **TradeRecorder simulated-flag support** — `include_simulated=False`
      default on `get_trades_by()`. P&L paths stay safe. Learning-loop
      consumers opt in.

  0b. **60d backfill seed script** — `scripts/seed_simulated_trades.py`
      runs `spy_daily_backtest` over last 90 calendar days, transforms
      results to TradeRecorder schema, persists `logs/simulated_trades.json`.
      Foundation for items 2 and 5.

  1.  **Prompt caching** — `call_llm` gains `cache_static_system: bool`
      and `model_preference: str`. All four Sonnet callers (reflector,
      hypothesis_engine, off_hours_learner, ai_advisor) refactored to use
      `call_llm` with caching. ~25-30% input-cost cut expected.

  2.  **rolling_accuracy per-sub-strategy** — `PredictionLog.accuracy()`
      gains `by_substrategy: bool`. Dict keyed by `strategy:dte_bucket:book`
      with `all` aggregate. MIN_SAMPLES=3 floor to avoid noise. Consumed
      by hypothesis_engine and reflector.

  3+4.**KB validator** — `learning/kb_validator.py` runs after Sonnet/phi4
      reply parses. Items 3 and 4 ship as one validator pass:
      - Confidence on `kind="daily"` capped at 0.7 (config constant)
      - Evidence-citation: must reference trade_id, today number (±0.1%),
        or kb_<id>. Soft enforcement — violations logged + marked, entry
        kept. Tightening to hard rejection deferred to Phase 4b after
        observing rate.

  5.  **Local-first reflector with anomaly hybrid** — `learning/anomaly_detector.py`
      pure function inspects today's facts. Triggers (all config constants):
      ≥2 stops, |prediction miss| > 1.5%, new sub-strategy fired, regime
      change vs yesterday. Normal days route to phi4 (Ollama); anomalous
      days route to Sonnet. Routing recorded as `result["route"]` for
      weekly telemetry. First-Tuesday expected 80-100% escalation as
      sub-strategies fire for the first time; steady-state target 20-40%
      by week 4.

  6.  **Off-hours learner pivot** — replaces near-miss threshold tuning
      (redundant with hypothesis_engine) with 60v60 regime-drift detection
      + feature trends. Catches meta-shifts that affect all sub-strategies.
      ≥10pt distribution shift threshold. KB entries tagged
      `kind=regime_drift`. Walk-forward boundary preserved: observation
      only, no parameter selection from drift findings — those go through
      hypothesis_engine's OOS gate.

**Tests:** ~818 passing (baseline 771 + ~47 new), 0 failures.

**Live behavior change:** None. All changes are inside the learning loop.
Phase 3's `INTRADAY_PAPER_BROKER_ENABLED` kill switch remains the sole
gate on live trading behavior.

**Phase 4b queue (cron reminder Sat 2026-05-30 10:03):**
- Path 1 full-fidelity 60d Phase 3 pipeline replay with real Polygon prices
- Per-sub-strategy structure builder (replaces Phase 3 placeholder pricing)
- Dual-book design with exit-feasibility predicate
- Per-sub-strategy reflector summarization
- Off-hours learner Option A (cross-sub-strategy pattern detection)
- Tighten KB validator item 4 to hard rejection if observed violation rate < 5%

**Cost observations:** Expected ~25-30% Sonnet-input reduction from caching
(item 1) + additional ~60-70% reduction from phi4-first routing (item 5)
on normal days. Combined: ~75-85% reflector cost cut once steady-state.
Polygon ($99/mo) still dominates total project cost.
```

- [ ] **Step 6: Commit final BUILD_LOG**

```bash
git add BUILD_LOG.md
git commit -m "docs: BUILD_LOG — Phase 4a complete (learning-loop hygiene)

7 items shipped across 8 tasks. Zero live-behavior change. ~47 new
tests. Phase 4b queued for Sat 2026-05-30 reminder.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: Confirm clean branch state**

```bash
git status
git log --oneline main..HEAD
```
Expected: clean working tree, 8 new commits on the phase4a branch ahead of main.

---

## Self-Review

**Spec coverage:** Every item from the spec maps to a task:
- Spec Item 0 (60d backfill) → Tasks 1 + 2
- Spec Item 1 (prompt caching) → Task 3
- Spec Item 2 (rolling_accuracy) → Task 4
- Spec Items 3+4 (KB validator) → Task 5
- Spec Item 5 (local-first reflector) → Task 6
- Spec Item 6 (off-hours regime drift) → Task 7
- Spec test coverage requirements → distributed across all tasks (~47 new tests)
- Risk register items → addressed in implementation (anomaly tunable via config, parity gates, soft enforcement, min-sample floor)

**Placeholder scan:** No "TBD" / "TODO" / "implement later" in any task. Where the implementer must read existing code to ground a change (e.g., `predictions.py` accuracy signature), the step explicitly says so and gives a fallback. Code blocks present in every code-changing step.

**Type consistency:**
- `call_llm` signature additions (`cache_static_system: bool`, `model_preference: str`) used identically across Tasks 3, 6, 7
- `simulated: True` flag used identically across Tasks 1, 2, 6 (Task 6's `_historical_substrategies` reads `include_simulated=True`)
- `kind="daily"` / `kind="regime_drift"` strings used identically across Tasks 5 and 7
- `is_anomalous_day(facts: dict) -> bool` signature consistent across Tasks 6 (creation) and any callers
- `validate_kb_entries(parsed, facts, default_kind)` signature consistent across Tasks 5 (creation) and 6 (use in reflector.reflect_today)

**Known plan-vs-reality gaps the implementer must handle:**
- Task 3 Step 1: implementer reads real `data/llm_client.py` to confirm signature
- Task 3 Step 9: implementer reads `alerts/ai_advisor.py` to determine if refactor is needed
- Task 4 Step 1: implementer reads `learning/predictions.py` to confirm `accuracy()` and `add()` signatures
- Task 7 Step 5: implementer searches for any old near-miss tests and updates/removes them

All four are explicit in the relevant steps. The implementer subagent will surface any plan deviation as `DONE_WITH_CONCERNS` or `NEEDS_CONTEXT`.
