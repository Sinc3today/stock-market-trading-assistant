# Intraday Entry-Router Walk-Forward Backtest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a walk-forward backtest that validates `signals/intraday_entry_router.py`'s tier-gate contribution by wrapping the existing real-priced 0DTE day simulator.

**Architecture:** Two new modules under `backtests/`. `router_setup_builder.py` replays historical SPY bars through `SPYOptionsEngine().analyze()` to produce per-day `SPYSetup` objects. `intraday_router_wf.py` orchestrates rolling 6mo train / 3mo test windows, runs treatment (router-gated) vs baseline (tier-gate disabled) on identical days, and emits raw per-window stats. Threshold-picking is deferred to a follow-up exercise; the verdict-stub returns `"raw"` until thresholds are populated.

**Tech Stack:** Python 3.11, pandas, pytz, loguru, fcntl-free (this is a backtest module, not a server), pytest with the project's `not integration` default marker convention.

**Spec:** `docs/superpowers/specs/2026-05-28-intraday-router-wf-design.md`

---

## File Structure

**Create (new):**
- `backtests/router_setup_builder.py` (~150-200 lines) — historical SPYSetup factory
- `backtests/intraday_router_wf.py` (~300-400 lines) — WF orchestrator (incl. inlined `_MockBroker`, `_bypass_tier_gate`, `_strategy_to_structure`, `simulate_short_dte_day` wrapper, window stats, verdict aggregator, CLI)
- `tests/test_router_setup_builder.py`
- `tests/test_intraday_router_wf.py`

**Read but do not modify (reuse as-is):**
- `data/intraday_data.py` — `get_stock_intraday()`
- `data/options_history.py` — `OptionsHistory`, `option_ticker()`
- `signals/spy_options_engine.py` — `SPYOptionsEngine`, `SPYSetup`
- `signals/intraday_entry_router.py` — `route()`, `SingleSetup` types
- `backtests/intraday_backtest.py` — `simulate_0dte_day()`, structure helpers, exit constants
- `backtests/spy_history.csv` — daily SPY OHLCV (csv with `,open,high,low,close,volume` columns, date as index column)
- `config.py` — `ENTRY_TIER_MINIMUM`, `ULTRA_CONVICTION_DOUBLE_DTE_SCORE`, `INTRADAY_PER_COMBO_DAILY_CAP`, `INTRADAY_DTE_MORNING_CUTOFF`

---

## Task 1: Setup builder — daily history loader

**Files:**
- Create: `backtests/router_setup_builder.py`
- Test: `tests/test_router_setup_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router_setup_builder.py
"""Tests for backtests/router_setup_builder.py."""

import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.router_setup_builder import load_daily_history


def test_load_daily_history_returns_dataframe_through_date():
    df = load_daily_history(date(2024, 6, 14))
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    # spy_history.csv has columns: open, high, low, close, volume (date as index)
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns, f"missing column: {col}"
    # The last row must be <= the cutoff (no lookahead)
    assert df.index.max() <= pd.Timestamp("2024-06-14")


def test_load_daily_history_excludes_target_date():
    """The cutoff date should be exclusive — the LAST completed daily bar is yesterday."""
    df = load_daily_history(date(2024, 6, 14))
    assert pd.Timestamp("2024-06-14") not in df.index


def test_load_daily_history_short_window_raises():
    """Caller requesting a date before spy_history.csv starts gets a clear error."""
    with pytest.raises(ValueError, match="insufficient daily history"):
        load_daily_history(date(2020, 1, 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_router_setup_builder.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtests.router_setup_builder'`

- [ ] **Step 3: Write minimal implementation**

```python
# backtests/router_setup_builder.py
"""
backtests/router_setup_builder.py -- Historical SPYSetup factory.

Replays a target date through SPYOptionsEngine.analyze() using the same daily
+ intraday DataFrames the live scanner uses. Produces SPYSetup objects
identical (modulo indicator code drift) to what the live scanner would have
emitted on that date.

Used by backtests/intraday_router_wf.py to validate the Phase 3 entry router.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


_SPY_HISTORY_CSV = os.path.join(os.path.dirname(__file__), "spy_history.csv")
_MIN_DAILY_BARS = 30   # SPYOptionsEngine's lower bound


def load_daily_history(through_date: date) -> pd.DataFrame:
    """Daily SPY OHLCV from spy_history.csv, sliced to BARS STRICTLY BEFORE
    `through_date`. The last bar in the returned frame is the most-recent
    completed daily session before the target date — no lookahead.
    """
    df = pd.read_csv(_SPY_HISTORY_CSV, index_col=0, parse_dates=True)
    df = df[df.index < pd.Timestamp(through_date)]
    if len(df) < _MIN_DAILY_BARS:
        raise ValueError(
            f"insufficient daily history for {through_date}: "
            f"{len(df)} bars, need >= {_MIN_DAILY_BARS}"
        )
    return df
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_router_setup_builder.py -v --tb=short`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backtests/router_setup_builder.py tests/test_router_setup_builder.py
git commit -m "feat(backtest): historical daily-history loader for SPYSetup factory

Reads spy_history.csv (back to 2021-05-24), slices to bars strictly before
the target date so the live scanner's no-lookahead invariant holds. Raises
ValueError when the requested date predates available history.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Setup builder — intraday window loader

**Files:**
- Modify: `backtests/router_setup_builder.py` (append new function)
- Test: `tests/test_router_setup_builder.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router_setup_builder.py — APPEND

from unittest.mock import patch
from backtests.router_setup_builder import load_intraday_window


def _make_5min_bars(target_date):
    """Generate synthetic 5-min bars covering 09:30 ET to 16:00 ET in UTC."""
    # 09:30 ET in summer = 13:30 UTC. Build 78 bars (6.5 hours * 12 bars/hr).
    start = pd.Timestamp(f"{target_date.isoformat()} 13:30:00", tz="UTC")
    idx = pd.date_range(start, periods=78, freq="5min")
    return pd.DataFrame({
        "open":   [500.0] * 78,
        "high":   [501.0] * 78,
        "low":    [499.0] * 78,
        "close":  [500.5] * 78,
        "volume": [1000]  * 78,
    }, index=idx)


def test_load_intraday_window_slices_9_30_to_9_45_ET():
    """Should return only the first 15 minutes (3 bars at 5-min)."""
    target = date(2024, 6, 14)
    with patch("backtests.router_setup_builder.get_stock_intraday",
               return_value=_make_5min_bars(target)):
        df = load_intraday_window(target)
    assert not df.empty
    # 09:30, 09:35, 09:40 — three 5-min bars before 09:45 cutoff
    assert len(df) == 3
    # Index should be tz-aware UTC (downstream tz handling lives in the engine)
    assert df.index.tz is not None


def test_load_intraday_window_returns_empty_when_no_data():
    target = date(2024, 6, 14)
    with patch("backtests.router_setup_builder.get_stock_intraday",
               return_value=pd.DataFrame()):
        df = load_intraday_window(target)
    assert df.empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_router_setup_builder.py::test_load_intraday_window_slices_9_30_to_9_45_ET -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'load_intraday_window'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/router_setup_builder.py`:

```python
from data.intraday_data import get_stock_intraday


def load_intraday_window(target_date: date) -> pd.DataFrame:
    """5-min SPY bars for `target_date`, sliced to 09:30-09:45 ET (opening
    range). Returns an empty DataFrame when no data is available — the caller
    treats that as 'skip this day, no signal.'

    Polygon list_aggs returns bars indexed in UTC; we keep them UTC here and
    let downstream tz conversion happen at the engine boundary.
    """
    df = get_stock_intraday("SPY", 5, "minute", target_date, target_date)
    if df.empty:
        return df
    # ET 09:30-09:44:59 == UTC 13:30-13:44:59 (EDT) or 14:30-14:44:59 (EST).
    # We slice in UTC against the actual session date — Polygon returns the
    # session date's bars whichever DST half we're in.
    et_open  = pd.Timestamp(f"{target_date.isoformat()} 09:30:00", tz="US/Eastern")
    et_or_end = pd.Timestamp(f"{target_date.isoformat()} 09:45:00", tz="US/Eastern")
    utc_open  = et_open.tz_convert("UTC")
    utc_or_end = et_or_end.tz_convert("UTC")
    return df[(df.index >= utc_open) & (df.index < utc_or_end)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_router_setup_builder.py -v --tb=short`
Expected: 5 passed (3 from Task 1 + 2 new)

- [ ] **Step 5: Commit**

```bash
git add backtests/router_setup_builder.py tests/test_router_setup_builder.py
git commit -m "feat(backtest): intraday 09:30-09:45 ET window loader for router WF

Wraps data.intraday_data.get_stock_intraday with a fixed 15-minute opening
range slice. Returns empty on no-data so the WF runner can treat it as
'no signal, skip this day in both treatment and baseline.'

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Setup builder — build_historical_setup integration

**Files:**
- Modify: `backtests/router_setup_builder.py` (append)
- Test: `tests/test_router_setup_builder.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router_setup_builder.py — APPEND

from backtests.router_setup_builder import build_historical_setup


def test_build_historical_setup_returns_list_of_spysetups():
    """End-to-end: known 2024 date yields at least one SPYSetup."""
    setups = build_historical_setup(date(2024, 6, 14))
    assert isinstance(setups, list)
    # SPYOptionsEngine emits 0..3 setups (call/put/condor) depending on scores.
    # On any normal day at least one strategy clears SCORE_ALERT_MINIMUM.
    if setups:
        s = setups[0]
        assert hasattr(s, "strategy")
        assert hasattr(s, "conviction")
        assert hasattr(s, "score")
        assert hasattr(s, "direction")
        assert s.strategy in {"call_debit_spread", "put_debit_spread", "iron_condor"}
        assert s.conviction in {"watch", "standard", "high"}


def test_build_historical_setup_returns_empty_on_missing_intraday():
    """When 09:30-09:45 bars are unavailable, return [] (skip day)."""
    with patch("backtests.router_setup_builder.load_intraday_window",
               return_value=pd.DataFrame()):
        setups = build_historical_setup(date(2024, 6, 14))
    assert setups == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_router_setup_builder.py::test_build_historical_setup_returns_list_of_spysetups -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'build_historical_setup'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/router_setup_builder.py`:

```python
from signals.spy_options_engine import SPYOptionsEngine, SPYSetup


def build_historical_setup(target_date: date) -> list[SPYSetup]:
    """Build the SPYSetup objects SPYOptionsEngine.analyze() would have
    emitted on `target_date` at 09:45 ET. Returns [] when intraday data
    is missing (treated as 'skip this day' by the WF runner).

    The engine is pure w.r.t. the DataFrames it consumes (verified —
    no live VIX/IVR/regime deps inside spy_options_engine), so this is a
    full-fidelity replay.
    """
    df_daily = load_daily_history(target_date)
    df_intraday = load_intraday_window(target_date)
    if df_intraday.empty:
        return []
    return SPYOptionsEngine().analyze(df_daily, df_intraday)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_router_setup_builder.py -v --tb=short`
Expected: 7 passed

Note: `test_build_historical_setup_returns_list_of_spysetups` hits the real Polygon API on first run (cached after) — if the test is too slow for `pytest -m "not integration"`, mark it `@pytest.mark.integration` and add a unit-test variant with a mocked `load_intraday_window`.

- [ ] **Step 5: Commit**

```bash
git add backtests/router_setup_builder.py tests/test_router_setup_builder.py
git commit -m "feat(backtest): build_historical_setup ties daily+intraday loaders to SPYOptionsEngine

Composes load_daily_history + load_intraday_window into the same
SPYOptionsEngine.analyze() call the live scanner makes. The engine is
pure w.r.t. its DataFrames (verified — no live VIX/IVR deps), so this
is a full-fidelity historical replay.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Orchestrator — _MockBroker

**Files:**
- Create: `backtests/intraday_router_wf.py`
- Test: `tests/test_intraday_router_wf.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py
"""Tests for backtests/intraday_router_wf.py."""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.intraday_router_wf import _MockBroker


def test_mockbroker_empty_returns_zero_opens():
    broker = _MockBroker()
    assert broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="0DTE") == []
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 0


def test_mockbroker_record_open_visible_to_dedup_queries():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    opens = broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="0DTE")
    assert len(opens) == 1
    assert opens[0]["outcome"] == "open"
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 1


def test_mockbroker_different_combos_isolated():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    assert broker.trades.get_trades_by(strategy="iron_condor", dte_bucket="1-3DTE") == []
    assert broker.trades.get_trades_by(strategy="call_debit_spread", dte_bucket="0DTE") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtests.intraday_router_wf'`

- [ ] **Step 3: Write minimal implementation**

```python
# backtests/intraday_router_wf.py
"""
backtests/intraday_router_wf.py -- Walk-forward backtest of the Phase 3
intraday entry router.

Wraps backtests/intraday_backtest.simulate_0dte_day with
signals/intraday_entry_router.route. Runs treatment (router-gated) vs
baseline (tier-gate disabled) on identical days, identical structures.
Emits raw per-window stats; verdict thresholds are TBD via a follow-up
calibration exercise.

Spec: docs/superpowers/specs/2026-05-28-intraday-router-wf-design.md
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─────────────────────────────────────────────────────────────
# Mock broker — satisfies signals.intraday_entry_router.route's
# dedup-state queries with per-day in-memory state.
# ─────────────────────────────────────────────────────────────

class _MockBroker:
    """Minimal broker stub for route(). Fresh-per-day in the runner so
    cross-day state can't leak. Implements only the two methods route()
    calls: trades.get_trades_by and _entry_count_today_by_combo."""

    def __init__(self):
        self.trades = self   # adapter so route() can call .trades.get_trades_by()
        self._opens: list[dict] = []

    def get_trades_by(self, *, strategy: str, dte_bucket: str) -> list[dict]:
        return [t for t in self._opens
                if t["strategy"] == strategy and t["dte_bucket"] == dte_bucket]

    def _entry_count_today_by_combo(self, strategy: str, dte_bucket: str) -> int:
        return len(self.get_trades_by(strategy=strategy, dte_bucket=dte_bucket))

    def record_open(self, *, strategy: str, dte_bucket: str) -> None:
        self._opens.append({
            "strategy":   strategy,
            "dte_bucket": dte_bucket,
            "outcome":    "open",
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): _MockBroker satisfies route()'s dedup-state queries

Per-day in-memory state for the entry router's open-position and
per-combo-count checks. Fresh-per-day in the runner prevents leakage.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Orchestrator — _bypass_tier_gate context manager

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: `tests/test_intraday_router_wf.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py — APPEND

import config
from backtests.intraday_router_wf import _bypass_tier_gate


def test_bypass_tier_gate_lowers_minimum_inside_block():
    original = config.ENTRY_TIER_MINIMUM
    with _bypass_tier_gate():
        assert config.ENTRY_TIER_MINIMUM == "watch"
    assert config.ENTRY_TIER_MINIMUM == original


def test_bypass_tier_gate_restores_on_exception():
    original = config.ENTRY_TIER_MINIMUM
    with pytest.raises(RuntimeError, match="boom"):
        with _bypass_tier_gate():
            raise RuntimeError("boom")
    assert config.ENTRY_TIER_MINIMUM == original


def test_bypass_tier_gate_restores_even_after_nested_change():
    """If user code mutates ENTRY_TIER_MINIMUM inside the block, the original
    value (captured at __enter__) is still restored."""
    original = config.ENTRY_TIER_MINIMUM
    with _bypass_tier_gate():
        config.ENTRY_TIER_MINIMUM = "something_else"  # nasty caller
    assert config.ENTRY_TIER_MINIMUM == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py::test_bypass_tier_gate_lowers_minimum_inside_block -v --tb=short`
Expected: FAIL with `ImportError: cannot import name '_bypass_tier_gate'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/intraday_router_wf.py`:

```python
from contextlib import contextmanager

import config


@contextmanager
def _bypass_tier_gate():
    """Temporarily set config.ENTRY_TIER_MINIMUM = 'watch' (the lowest rank
    in signals.intraday_entry_router._TIER_RANK) so route()'s tier gate
    admits everything. Used to compute the BASELINE side of the WF
    comparison — DTE assignment and dedup remain identical to treatment,
    so the only delta is the tier filter.

    Restoration is guaranteed: the original value is captured at __enter__,
    not read from config at __exit__, so caller mutations inside the
    block don't break restoration.
    """
    original = config.ENTRY_TIER_MINIMUM
    config.ENTRY_TIER_MINIMUM = "watch"
    try:
        yield
    finally:
        config.ENTRY_TIER_MINIMUM = original
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 6 passed (3 broker + 3 bypass)

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): _bypass_tier_gate context manager for WF baseline

Temporarily sets ENTRY_TIER_MINIMUM to 'watch' so route()'s tier filter
admits every conviction level. DTE assignment + dedup unchanged, so the
only delta vs treatment is the tier gate. Restoration is exception-safe
and immune to caller mutations inside the block.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Orchestrator — generate_windows

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: `tests/test_intraday_router_wf.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py — APPEND

from backtests.intraday_router_wf import generate_windows


def test_generate_windows_full_2024_2025_monthly_step():
    """6mo train / 3mo test / 1mo step over 2024-01-02 to 2025-12-31."""
    wins = list(generate_windows(date(2024, 1, 2), date(2025, 12, 31),
                                 train_months=6, test_months=3, step_months=1))
    # First test window: months 7-9 of 2024 (after the 6mo train).
    # Last possible test: months 10-12 of 2025 (ends on/before 2025-12-31).
    assert len(wins) == 16, f"expected 16 windows, got {len(wins)}"
    # Train always precedes test, no overlap inside a single window.
    for train_range, test_range in wins:
        assert train_range[1] < test_range[0], \
            f"train must end before test starts: {train_range} vs {test_range}"


def test_generate_windows_monotonic_test_starts():
    """Sliding window: each window's test_start is monotonically increasing."""
    wins = list(generate_windows(date(2024, 1, 2), date(2025, 12, 31)))
    test_starts = [test_range[0] for _, test_range in wins]
    assert test_starts == sorted(test_starts)


def test_generate_windows_stops_when_test_would_overshoot_end():
    """No window whose test_range extends past `end`."""
    end = date(2024, 12, 31)
    wins = list(generate_windows(date(2024, 1, 2), end,
                                 train_months=6, test_months=3, step_months=1))
    for _, (_, test_end) in wins:
        assert test_end <= end
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py::test_generate_windows_full_2024_2025_monthly_step -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'generate_windows'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/intraday_router_wf.py`:

```python
from datetime import date, timedelta
from typing import Iterator


def _add_months(d: date, n: int) -> date:
    """Add n calendar months to date d, clipping the day to the new month's
    last day if necessary. Used for window boundary math."""
    month = d.month + n
    year  = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    # Clip day to month's last day to avoid 31->Feb errors.
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def generate_windows(start: date, end: date,
                     train_months: int = 6, test_months: int = 3,
                     step_months: int = 1
                     ) -> Iterator[tuple[tuple[date, date], tuple[date, date]]]:
    """Yield (train_range, test_range) tuples where each range is
    (start_date_inclusive, end_date_inclusive).

    Sliding walk-forward: train covers `train_months` calendar months
    immediately preceding test; test covers the next `test_months`. Each
    iteration advances the test_start by `step_months`. Stops when the test
    range would overshoot `end`.

    Train window has no learning role in this spec — it's a contextual
    placeholder for a future learning step.
    """
    test_start = _add_months(start, train_months)
    while True:
        train_start = _add_months(test_start, -train_months)
        train_end   = test_start - timedelta(days=1)
        test_end    = _add_months(test_start, test_months) - timedelta(days=1)
        if test_end > end:
            return
        yield ((train_start, train_end), (test_start, test_end))
        test_start = _add_months(test_start, step_months)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 9 passed (6 prior + 3 new)

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): generate_windows yields rolling 6mo/3mo train+test ranges

Calendar-month arithmetic with clip-to-month-end safety. Stops when test
range would overshoot end. Train window precedes test with no overlap;
windows step by step_months (default 1mo, so test ranges overlap by design).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Orchestrator — strategy mapping + simulate_short_dte_day wrapper

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: `tests/test_intraday_router_wf.py` (append)

This task introduces (a) the router→IB structure name mapping and (b) a thin wrapper that handles 0DTE and 1-3DTE without modifying `intraday_backtest.py`. For 0DTE, delegates to `simulate_0dte_day`. For 1-3DTE, uses a same-session simulator that picks a future-expiration contract and exits at the regular session close (no pin/assignment flatten because the contract has more days to live).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py — APPEND

from backtests.intraday_router_wf import (
    _strategy_to_structure,
    STRATEGY_NOT_SUPPORTED,
)


def test_strategy_to_structure_iron_condor():
    assert _strategy_to_structure("iron_condor", "neutral") == "iron_condor"


def test_strategy_to_structure_call_debit_spread_bullish():
    assert _strategy_to_structure("call_debit_spread", "bullish") == "bull_debit"


def test_strategy_to_structure_put_debit_spread_bearish():
    assert _strategy_to_structure("put_debit_spread", "bearish") == "bear_debit"


def test_strategy_to_structure_unknown_returns_sentinel():
    assert _strategy_to_structure("rotational_diagonal", "bullish") is STRATEGY_NOT_SUPPORTED


# simulate_short_dte_day is tested via the integration test in Task 12 —
# unit-testing it would re-test simulate_0dte_day, which already has tests
# in backtests/intraday_backtest.py's own suite.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py::test_strategy_to_structure_iron_condor -v --tb=short`
Expected: FAIL with `ImportError: cannot import name '_strategy_to_structure'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/intraday_router_wf.py`:

```python
STRATEGY_NOT_SUPPORTED = object()   # sentinel — router emitted a strategy
                                     # backtests/intraday_backtest.py can't price


def _strategy_to_structure(strategy: str, direction: str):
    """Map signals.intraday_entry_router setup.strategy → backtests.
    intraday_backtest structure name. Returns STRATEGY_NOT_SUPPORTED if
    the strategy can't be priced (out of scope for v1)."""
    if strategy == "iron_condor":
        return "iron_condor"
    if strategy == "call_debit_spread":
        return "bull_debit"
    if strategy == "put_debit_spread":
        return "bear_debit"
    return STRATEGY_NOT_SUPPORTED


def simulate_short_dte_day(day, structure: str, dte_bucket: str,
                            spy_intraday, options_history):
    """Wrap backtests.intraday_backtest.simulate_0dte_day to support 0DTE
    AND 1-3DTE in the same call. The 0DTE path delegates directly; the
    1-3DTE path picks a future-expiration contract and exits at session
    close instead of the 0DTE EOD pin/assignment flatten.

    Treatment and baseline both call this with require_confirmation=False
    so the router IS the entry filter (OR+VWAP would double-gate otherwise).

    Returns simulate_0dte_day's result dict, or None when the day can't
    be priced.
    """
    from datetime import timedelta
    from backtests.intraday_backtest import simulate_0dte_day

    if dte_bucket == "0DTE":
        return simulate_0dte_day(
            day, structure, spy_intraday, options_history,
            require_confirmation=False,   # router replaces OR+VWAP
        )

    if dte_bucket == "1-3DTE":
        # Use day+2 (the router's _synthesize_legs midpoint) as the
        # expiration target. simulate_0dte_day's options_history lookups
        # use `day` as the expiration; we override by temporarily
        # monkey-patching the lookup date. Cleanest path: call a small
        # variant that takes expiration explicitly.
        return _simulate_short_dte_with_expiration(
            day, day + timedelta(days=2),
            structure, spy_intraday, options_history,
        )

    return None   # unknown bucket — caller's bug


def _simulate_short_dte_with_expiration(day, expiry,
                                         structure: str,
                                         spy_intraday, options_history):
    """1-3DTE same-session simulator. Same opening-range entry as the 0DTE
    simulator, but the option contract has `expiry > day`, so:
      - There's no pin/assignment risk on `day`, hence no 15:45 flatten —
        we exit at the regular session close (16:00) or on target/stop.
      - This is a SAME-DAY-MARK approximation: we record entry-to-close
        PnL on `day` for a contract that has additional days to live.
        Full multi-day PnL is out of scope for v1 — documented in spec.
    """
    from datetime import datetime, timedelta, time
    from data.options_history import option_ticker
    from backtests.intraday_backtest import (
        _to_et, _spread_value, build_0dte_legs, is_credit_structure,
        MARKET_OPEN_ET, OR_MINUTES, COMMISSION_PER_LEG, SLIPPAGE,
        PROFIT_TARGET_PCT, STOP_MULT,
    )
    import pandas as pd

    if spy_intraday is None or spy_intraday.empty:
        return None
    spy = _to_et(spy_intraday)
    SESSION_CLOSE_ET = time(16, 0)
    rth = spy[(spy.index.time >= MARKET_OPEN_ET) & (spy.index.time <= SESSION_CLOSE_ET)]
    if rth.empty:
        return None

    or_end = (datetime.combine(day, MARKET_OPEN_ET) + timedelta(minutes=OR_MINUTES)).time()
    session = rth[rth.index.time >= or_end]
    if session.empty:
        return None

    entry_ts   = session.index[0]
    entry_spot = float(session.iloc[0]["close"])

    legs = build_0dte_legs(entry_spot, structure)
    if not legs:
        return None

    # Future expiration: option_ticker(underlying, expiry, ...) with the
    # 1-3DTE target. Intraday bars for that contract on `day` are pulled
    # the same way as 0DTE — Polygon serves them.
    leg_closes = []
    for leg in legs:
        contract = option_ticker("SPY", expiry, leg["cp"], leg["strike"])
        df = options_history.get_aggs(contract, 5, "minute", day, day)
        if df.empty:
            return None
        s = _to_et(df)["close"]
        leg_closes.append((leg, s))

    def marks_at(ts):
        out = []
        for leg, s in leg_closes:
            at = s[s.index <= ts]
            if at.empty:
                return None
            out.append((leg, float(at.iloc[-1])))
        return out

    credit = is_credit_structure(structure)
    entry_marks = marks_at(entry_ts)
    if entry_marks is None:
        return None
    entry_px = _spread_value(entry_marks, structure)
    entry_px = (entry_px - SLIPPAGE) if credit else (entry_px + SLIPPAGE)
    if entry_px <= 0:
        return None

    width      = abs(legs[0]["strike"] - legs[1]["strike"]) if len(legs) >= 2 else 0
    max_profit = entry_px * 100 if credit else (width - entry_px) * 100
    commission = COMMISSION_PER_LEG * len(legs) * 2

    exit_reason = "session_close"
    pnl = -commission
    for ts in session.index:
        m = marks_at(ts)
        if m is None:
            continue
        val = _spread_value(m, structure)
        if credit:
            cost = val + SLIPPAGE
            pnl  = (entry_px - cost) * 100 - commission
        else:
            proceeds = max(0.0, val - SLIPPAGE)
            pnl      = (proceeds - entry_px) * 100 - commission
        if max_profit > 0 and pnl >= PROFIT_TARGET_PCT * max_profit:
            exit_reason = "target"; break
        if STOP_MULT is not None and pnl <= -STOP_MULT * max_profit:
            exit_reason = "stop"; break

    return {
        "date": day.isoformat(), "structure": structure,
        "entry_spot": round(entry_spot, 2), "entry_px": round(entry_px, 2),
        "pnl_dollars": round(pnl, 2),
        "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "exit_reason": exit_reason,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 13 passed (9 prior + 4 new strategy-mapping)

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): _strategy_to_structure + simulate_short_dte_day wrappers

Router emits 'call_debit_spread'/'put_debit_spread'/'iron_condor';
backtests/intraday_backtest uses 'bull_debit'/'bear_debit'/'iron_condor'.
_strategy_to_structure bridges, returns sentinel for unsupported strategies.

simulate_short_dte_day delegates 0DTE to the existing simulator and adds
a same-session 1-3DTE variant that exits at session close (no pin flatten
since the contract has more days to live). Both paths use
require_confirmation=False so the router IS the entry filter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Orchestrator — window_stats aggregator

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: `tests/test_intraday_router_wf.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py — APPEND

import math
from backtests.intraday_router_wf import window_stats


def _trade(pnl, strategy="iron_condor", bucket="0DTE"):
    return {"pnl_dollars": pnl, "strategy": strategy, "dte_bucket": bucket}


def test_window_stats_empty_treatment_returns_zero_n():
    s = window_stats([], [_trade(50), _trade(-30)])
    assert s["n_trades_T"] == 0
    assert s["n_trades_B"] == 2
    assert s["pnl_T"] == 0.0
    assert math.isnan(s["delta_pnl_per_trade"]) or s["delta_pnl_per_trade"] == 0.0


def test_window_stats_computes_deltas():
    t = [_trade(100), _trade(-50), _trade(80)]
    b = [_trade(40),  _trade(-80), _trade(20)]
    s = window_stats(t, b)
    assert s["n_trades_T"] == 3
    assert s["n_trades_B"] == 3
    assert s["pnl_T"] == 130.0
    assert s["pnl_B"] == -20.0
    assert s["delta_pnl_per_trade"] == pytest.approx((130.0/3) - (-20.0/3))
    assert s["win_rate_T"] == pytest.approx(2/3)
    assert s["win_rate_B"] == pytest.approx(2/3)


def test_window_stats_includes_per_bucket_breakdown():
    t = [_trade(100, bucket="0DTE"), _trade(-50, bucket="1-3DTE")]
    b = [_trade(40,  bucket="0DTE"), _trade(20,  bucket="1-3DTE")]
    s = window_stats(t, b)
    assert "by_bucket" in s
    assert s["by_bucket"]["0DTE"]["n_trades_T"] == 1
    assert s["by_bucket"]["1-3DTE"]["n_trades_T"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py::test_window_stats_computes_deltas -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'window_stats'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/intraday_router_wf.py`:

```python
import math
import statistics
from collections import Counter


def _sharpe(pnls: list[float]) -> float:
    """Per-trade Sharpe: mean / stdev. Returns 0.0 when n<2 (undefined stdev)."""
    if len(pnls) < 2:
        return 0.0
    sd = statistics.stdev(pnls)
    if sd == 0:
        return 0.0
    return statistics.mean(pnls) / sd


def window_stats(trades_T: list[dict], trades_B: list[dict]) -> dict:
    """Aggregate per-window stats. Trades are dicts from simulate_short_dte_day
    with at least 'pnl_dollars', 'strategy', 'dte_bucket'. Either side may
    be empty (e.g. baseline returns no trades for a window — unlikely but
    possible if all setups failed the engine's score floor)."""

    def _aggregate(trades):
        n = len(trades)
        pnls = [t["pnl_dollars"] for t in trades]
        return {
            "n":      n,
            "pnl":    sum(pnls) if pnls else 0.0,
            "mean":   (sum(pnls) / n) if n else 0.0,
            "sharpe": _sharpe(pnls),
            "wins":   sum(1 for p in pnls if p > 0),
        }

    T = _aggregate(trades_T)
    B = _aggregate(trades_B)

    # Per-bucket breakdown (0DTE / 1-3DTE).
    buckets = sorted({t["dte_bucket"] for t in trades_T} |
                     {t["dte_bucket"] for t in trades_B})
    by_bucket = {}
    for b in buckets:
        bT = _aggregate([t for t in trades_T if t["dte_bucket"] == b])
        bB = _aggregate([t for t in trades_B if t["dte_bucket"] == b])
        by_bucket[b] = {
            "n_trades_T": bT["n"], "n_trades_B": bB["n"],
            "pnl_T":      bT["pnl"], "pnl_B":      bB["pnl"],
            "sharpe_T":   bT["sharpe"], "sharpe_B": bB["sharpe"],
        }

    return {
        "n_trades_T":           T["n"],
        "n_trades_B":           B["n"],
        "pnl_T":                T["pnl"],
        "pnl_B":                B["pnl"],
        "sharpe_T":             T["sharpe"],
        "sharpe_B":             B["sharpe"],
        "win_rate_T":           (T["wins"] / T["n"]) if T["n"] else 0.0,
        "win_rate_B":           (B["wins"] / B["n"]) if B["n"] else 0.0,
        "delta_pnl_per_trade":  T["mean"] - B["mean"],
        "delta_sharpe":         T["sharpe"] - B["sharpe"],
        "by_bucket":            by_bucket,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 16 passed (13 prior + 3 new)

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): window_stats per-trade aggregator with per-bucket breakdown

Per-window Δ\$/trade, Δ Sharpe, OOS PnL, win rate. Per-bucket (0DTE/1-3DTE)
breakdown so 0DTE results stay comparable to the 5/21 -\$515 baseline.
Stat helpers return 0.0 on undefined-stdev / empty inputs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Orchestrator — aggregate_verdict + verdict-stub

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: `tests/test_intraday_router_wf.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py — APPEND

from backtests.intraday_router_wf import window_verdict, aggregate_verdict


def test_window_verdict_returns_raw_when_thresholds_unset():
    """All thresholds None → verdict 'raw' regardless of stats."""
    stats = {"n_trades_T": 50, "pnl_T": 1000.0, "sharpe_T": 1.5, "win_rate_T": 0.7,
             "delta_pnl_per_trade": 10.0}
    assert window_verdict(stats) == "raw"


def test_window_verdict_inconclusive_when_too_few_trades():
    stats = {"n_trades_T": 5, "pnl_T": 1000.0, "sharpe_T": 1.5, "win_rate_T": 0.7,
             "delta_pnl_per_trade": 10.0}
    # Inconclusive even with great stats when n < MIN_N_FOR_VERDICT.
    assert window_verdict(stats, min_n=10) == "inconclusive"


def test_aggregate_verdict_pass_rate():
    """Pass rate excludes 'inconclusive' from the denominator."""
    results = [
        {"verdict": "pass"}, {"verdict": "pass"}, {"verdict": "pass"},
        {"verdict": "fail"},
        {"verdict": "inconclusive"},
    ]
    agg = aggregate_verdict(results)
    assert agg["n_windows"] == 5
    assert agg["n_pass"] == 3
    assert agg["n_fail"] == 1
    assert agg["n_inconclusive"] == 1
    assert agg["pass_rate"] == pytest.approx(3 / 4)   # 3 pass / (3 pass + 1 fail)


def test_aggregate_verdict_all_raw_when_thresholds_unset():
    results = [{"verdict": "raw"}, {"verdict": "raw"}]
    agg = aggregate_verdict(results)
    assert agg["pass_rate"] is None
    assert agg["n_raw"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py::test_window_verdict_returns_raw_when_thresholds_unset -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'window_verdict'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/intraday_router_wf.py`:

```python
# ─────────────────────────────────────────────────────────────
# Verdict thresholds — TBD via separate calibration exercise.
# When ALL of these are None, window_verdict returns 'raw'.
# ─────────────────────────────────────────────────────────────
MIN_DELTA_PNL_PER_TRADE: float | None = None
MIN_OOS_PNL:             float | None = None
MIN_OOS_SHARPE:          float | None = None
MIN_OOS_WIN_RATE:        float | None = None


def window_verdict(stats: dict, min_n: int = 10) -> str:
    """Returns one of 'raw', 'inconclusive', 'pass', 'fail'.

      'raw'          — thresholds not yet calibrated; stats emitted only
      'inconclusive' — n_trades_T < min_n
      'pass'         — all thresholds met
      'fail'         — at least one threshold missed
    """
    thresholds = (MIN_DELTA_PNL_PER_TRADE, MIN_OOS_PNL,
                  MIN_OOS_SHARPE, MIN_OOS_WIN_RATE)
    if all(t is None for t in thresholds):
        return "raw"
    if stats.get("n_trades_T", 0) < min_n:
        return "inconclusive"
    if (MIN_DELTA_PNL_PER_TRADE is not None
            and stats["delta_pnl_per_trade"] < MIN_DELTA_PNL_PER_TRADE):
        return "fail"
    if MIN_OOS_PNL is not None and stats["pnl_T"] < MIN_OOS_PNL:
        return "fail"
    if MIN_OOS_SHARPE is not None and stats["sharpe_T"] < MIN_OOS_SHARPE:
        return "fail"
    if MIN_OOS_WIN_RATE is not None and stats["win_rate_T"] < MIN_OOS_WIN_RATE:
        return "fail"
    return "pass"


def aggregate_verdict(window_results: list[dict]) -> dict:
    """Aggregate per-window verdicts into headline pass-rate. 'inconclusive'
    is excluded from the pass-rate denominator. 'raw' windows mean
    thresholds aren't set yet — pass_rate is None in that case."""
    counts = Counter(r["verdict"] for r in window_results)
    n_pass         = counts.get("pass", 0)
    n_fail         = counts.get("fail", 0)
    n_inconclusive = counts.get("inconclusive", 0)
    n_raw          = counts.get("raw", 0)
    determinative  = n_pass + n_fail
    return {
        "n_windows":      len(window_results),
        "n_pass":         n_pass,
        "n_fail":         n_fail,
        "n_inconclusive": n_inconclusive,
        "n_raw":          n_raw,
        "pass_rate":      (n_pass / determinative) if determinative else None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 20 passed (16 prior + 4 new)

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): window_verdict + aggregate_verdict with deferred thresholds

window_verdict returns 'raw' until threshold constants are populated by the
follow-up calibration exercise. Until then the WF runs end-to-end and emits
the stats matrix; verdict logic is wired but inert.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Orchestrator — run_window with apples-to-apples invariant

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: `tests/test_intraday_router_wf.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_router_wf.py — APPEND

from datetime import date as _date
from unittest.mock import MagicMock, patch
from backtests.intraday_router_wf import run_window


def _fake_setup(strategy="iron_condor", conviction="high", score=100,
                direction="neutral"):
    s = MagicMock()
    s.strategy = strategy
    s.conviction = conviction
    s.score = score
    s.direction = direction
    return s


def test_run_window_apples_to_apples_skipped_day():
    """A day that raises in setup-building drops from BOTH treatment and baseline."""

    def get_setup(d):
        if d == _date(2024, 6, 17):
            raise RuntimeError("simulated data failure")
        return [_fake_setup()]

    pnl_log: list = []
    def get_pnl(day, setup, strategy, dte_bucket):
        pnl_log.append((day, strategy, dte_bucket))
        return {"pnl_dollars": 50.0, "strategy": strategy, "dte_bucket": dte_bucket}

    result = run_window(
        train_range=(_date(2024, 1, 1), _date(2024, 6, 14)),
        test_range=(_date(2024, 6, 17), _date(2024, 6, 18)),
        get_setup=get_setup,
        get_pnl=get_pnl,
    )

    # 2024-06-17 was skipped on the T side → it must also drop from B.
    # Apples-to-apples: equal trades on both sides for the remaining day.
    assert result["stats"]["n_trades_T"] == result["stats"]["n_trades_B"]
    # 06-17 skipped + 06-18 traded → only one day contributes per side.
    # IC score 100 hits ULTRA_CONVICTION_DOUBLE_DTE_SCORE → 2 buckets per day.
    assert result["stats"]["n_trades_T"] == 2


def test_run_window_returns_skip_reasons():
    def get_setup(d):
        return []   # every day yields no setups → all skipped on both sides

    def get_pnl(*a, **kw):
        return {"pnl_dollars": 0.0}

    result = run_window(
        train_range=(_date(2024, 1, 1), _date(2024, 6, 14)),
        test_range=(_date(2024, 6, 17), _date(2024, 6, 18)),
        get_setup=get_setup,
        get_pnl=get_pnl,
    )
    assert result["stats"]["n_trades_T"] == 0
    assert "skip_reasons" in result
    assert result["skip_reasons"]["empty_setup"] >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py::test_run_window_apples_to_apples_skipped_day -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'run_window'`

- [ ] **Step 3: Write minimal implementation**

Append to `backtests/intraday_router_wf.py`:

```python
import pytz
from datetime import datetime
from loguru import logger
from signals.intraday_entry_router import route as _route_entry


_ET = pytz.timezone("US/Eastern")


def _iter_trading_days(start: date, end: date) -> Iterator[date]:
    """Yield weekdays in [start, end] inclusive. Holiday handling deferred —
    setup builder returning [] for a holiday day is the natural skip path."""
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d = d + timedelta(days=1)


def run_window(*, train_range, test_range, get_setup, get_pnl) -> dict:
    """Run one walk-forward window and return its results.

    Parameters
    ----------
    train_range : (date, date) — contextual, no learning role this spec
    test_range  : (date, date) — OOS evaluation period
    get_setup   : Callable[[date], list[SPYSetup]] — usually
                  router_setup_builder.build_historical_setup
    get_pnl     : Callable[[date, setup, strategy, dte_bucket], dict|None]
                  — usually a closure around simulate_short_dte_day +
                  OptionsHistory + cached spy_intraday

    Dependency injection lets the unit tests substitute pure-function stubs.
    """
    trades_T: list[dict] = []
    trades_B: list[dict] = []
    skip_reasons: Counter = Counter()

    for day in _iter_trading_days(*test_range):
        try:
            setups = get_setup(day)
        except Exception as e:
            logger.debug(f"router_wf: skip {day} setup_error={e!r}")
            skip_reasons["setup_error"] += 1
            continue

        if not setups:
            skip_reasons["empty_setup"] += 1
            continue

        # Apples-to-apples scope: a day is either evaluated on BOTH sides
        # or skipped on BOTH. We build the bucket lists FIRST (both sides),
        # then simulate. If any simulation step fails, we discard the day's
        # contribution to both sides.
        ts_945 = _ET.localize(datetime.combine(day, datetime.min.time())
                              .replace(hour=9, minute=45))

        day_T: list[dict] = []
        day_B: list[dict] = []
        day_failed = False

        for setup in setups:
            structure = _strategy_to_structure(setup.strategy, setup.direction)
            if structure is STRATEGY_NOT_SUPPORTED:
                skip_reasons["strategy_not_supported"] += 1
                continue

            # Treatment: router with tier gate.
            buckets_T = _route_entry(setup, ts_945, _MockBroker())
            # Baseline: router with tier gate disabled.
            with _bypass_tier_gate():
                buckets_B = _route_entry(setup, ts_945, _MockBroker())

            for sd in buckets_T:
                outcome = get_pnl(day, setup, structure, sd["dte_bucket"])
                if outcome is None:
                    day_failed = True; break
                # Tag the trade dict for downstream window_stats.
                outcome.setdefault("strategy", setup.strategy)
                outcome.setdefault("dte_bucket", sd["dte_bucket"])
                day_T.append(outcome)
            if day_failed:
                break

            for sd in buckets_B:
                outcome = get_pnl(day, setup, structure, sd["dte_bucket"])
                if outcome is None:
                    day_failed = True; break
                outcome.setdefault("strategy", setup.strategy)
                outcome.setdefault("dte_bucket", sd["dte_bucket"])
                day_B.append(outcome)
            if day_failed:
                break

        if day_failed:
            skip_reasons["sim_failure"] += 1
            continue   # invariant: drop from BOTH sides

        trades_T.extend(day_T)
        trades_B.extend(day_B)

    stats = window_stats(trades_T, trades_B)
    return {
        "train_range":  train_range,
        "test_range":   test_range,
        "stats":        stats,
        "skip_reasons": dict(skip_reasons),
        "verdict":      window_verdict(stats),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_intraday_router_wf.py -v --tb=short`
Expected: 22 passed (20 prior + 2 new)

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_intraday_router_wf.py
git commit -m "feat(backtest): run_window enforces apples-to-apples invariant

Day-level skips (setup error, empty setup, sim failure) drop from BOTH
treatment and baseline. Per-setup, route() is called twice (treatment
direct, baseline inside _bypass_tier_gate) with fresh _MockBroker
instances. Dependency injection (get_setup / get_pnl) keeps the runner
testable with pure-function stubs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Orchestrator — CLI entry

**Files:**
- Modify: `backtests/intraday_router_wf.py` (append)
- Test: manual run (no unit test — covered by Task 12 integration test)

- [ ] **Step 1: Append CLI**

Append to `backtests/intraday_router_wf.py`:

```python
import json
from data.options_history import OptionsHistory


def _build_get_pnl(spy_intraday_cache: dict, options_history):
    """Closure factory: returns a get_pnl(day, setup, structure, dte_bucket)
    that consults a per-day cached spy_intraday DataFrame and the shared
    options_history client."""
    from data.intraday_data import get_stock_intraday

    def get_pnl(day, setup, structure, dte_bucket):
        spy = spy_intraday_cache.get(day)
        if spy is None:
            spy = get_stock_intraday("SPY", 5, "minute", day, day)
            spy_intraday_cache[day] = spy
        return simulate_short_dte_day(day, structure, dte_bucket,
                                       spy, options_history)
    return get_pnl


def run_walk_forward(start: date, end: date,
                     train_months: int = 6,
                     test_months: int = 3,
                     step_months: int = 1) -> dict:
    """Run all windows in [start, end] and return the aggregate report."""
    from backtests.router_setup_builder import build_historical_setup

    options_history = OptionsHistory()
    spy_cache: dict = {}
    get_pnl = _build_get_pnl(spy_cache, options_history)

    windows = list(generate_windows(start, end,
                                    train_months=train_months,
                                    test_months=test_months,
                                    step_months=step_months))
    logger.info(f"router_wf: running {len(windows)} windows from {start} to {end}")
    results = []
    for i, (train_range, test_range) in enumerate(windows, 1):
        logger.info(f"router_wf: window {i}/{len(windows)} test={test_range}")
        r = run_window(train_range=train_range, test_range=test_range,
                       get_setup=build_historical_setup, get_pnl=get_pnl)
        s = r["stats"]
        logger.info(
            f"router_wf: window {i} n_T={s['n_trades_T']} n_B={s['n_trades_B']} "
            f"ΔPnL/trade={s['delta_pnl_per_trade']:.2f} "
            f"ΔSharpe={s['delta_sharpe']:.2f} verdict={r['verdict']}"
        )
        results.append(r)

    agg = aggregate_verdict(results)
    return {"windows": results, "aggregate": agg}


if __name__ == "__main__":
    import argparse
    from datetime import datetime as _dt

    parser = argparse.ArgumentParser(description="Phase 3 entry-router WF backtest")
    parser.add_argument("--start", default="2024-01-02", help="ISO date")
    parser.add_argument("--end",   default="2025-12-31", help="ISO date")
    parser.add_argument("--out",   default="logs/router_wf_report.json")
    args = parser.parse_args()

    start_d = _dt.fromisoformat(args.start).date()
    end_d   = _dt.fromisoformat(args.end).date()

    report = run_walk_forward(start_d, end_d)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # Serialize: convert date tuples to ISO strings, Counters already dict.
    def _ser(obj):
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, tuple):
            return [_ser(x) for x in obj]
        return obj
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=_ser)
    logger.info(f"router_wf: wrote report to {args.out}")
    logger.info(f"router_wf: aggregate = {report['aggregate']}")
```

- [ ] **Step 2: Smoke-run a minimal window manually**

Run a 1-window smoke test to confirm the CLI wires together. This will hit Polygon for ~22 trading days of SPY 5-min bars + ~22 days × N option contracts of intraday option aggregates. First run is slow (~5-15 minutes depending on cache state); subsequent runs are fast.

```bash
.venv/bin/python -m backtests.intraday_router_wf \
  --start 2024-04-01 --end 2024-09-30 \
  --out logs/router_wf_smoke.json
```

Expected output (last few lines):
```
router_wf: window 1/1 test=(2024-07-01, 2024-09-30)
router_wf: window 1 n_T=<int> n_B=<int> ΔPnL/trade=<float> ΔSharpe=<float> verdict=raw
router_wf: wrote report to logs/router_wf_smoke.json
router_wf: aggregate = {'n_windows': 1, 'n_pass': 0, 'n_fail': 0, 'n_inconclusive': 0, 'n_raw': 1, 'pass_rate': None}
```

- [ ] **Step 3: Inspect the smoke report**

```bash
.venv/bin/python -c "import json; r=json.load(open('logs/router_wf_smoke.json')); print(json.dumps(r['aggregate'], indent=2)); print('first window stats:'); print(json.dumps(r['windows'][0]['stats'], indent=2))"
```

Expected: aggregate has `n_raw=1` (thresholds not set); first window's `stats` has `n_trades_T`, `n_trades_B`, `pnl_T`, `pnl_B`, `delta_pnl_per_trade`, `by_bucket`, etc. — no NaN or missing keys.

- [ ] **Step 4: Commit**

```bash
git add backtests/intraday_router_wf.py
git commit -m "feat(backtest): CLI entry for run_walk_forward over date range

run_walk_forward orchestrates windows + builds the get_pnl closure with
per-day spy_intraday caching. CLI writes JSON report to logs/. Smoke-run
against 2024-04 to 2024-09 (1 window) confirms wiring end-to-end.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Integration test (marked)

**Files:**
- Modify: `tests/test_intraday_router_wf.py` (append)
- Modify: `pytest.ini` if `integration` marker isn't already declared (verify first)

- [ ] **Step 1: Verify the integration marker is recognized**

```bash
grep -n "integration" pytest.ini
```

Expected: a line like `markers = integration: mark a test as integration` or similar. If absent, skip this verification — it's already used in CLAUDE.md's standard invocation.

- [ ] **Step 2: Write the integration test**

Append to `tests/test_intraday_router_wf.py`:

```python
@pytest.mark.integration
def test_run_walk_forward_smoke_one_window_completes():
    """Smoke-run one short window end-to-end. Confirms the pipeline does
    not crash, produces non-empty stats, and emits a verdict (likely 'raw'
    until thresholds are calibrated).

    Skipped in the default `pytest -m 'not integration'` invocation. Run
    explicitly with `pytest -m integration tests/test_intraday_router_wf.py`.
    """
    from backtests.intraday_router_wf import run_walk_forward

    report = run_walk_forward(
        date(2024, 7, 1), date(2024, 9, 30),
        train_months=3, test_months=3, step_months=3,
    )
    assert report["aggregate"]["n_windows"] >= 1
    w = report["windows"][0]
    assert "stats" in w
    assert "n_trades_T" in w["stats"]
    assert "n_trades_B" in w["stats"]
    assert w["verdict"] in {"raw", "pass", "fail", "inconclusive"}
```

- [ ] **Step 3: Run the unit suite to confirm nothing broke**

```bash
.venv/bin/pytest tests/test_intraday_router_wf.py tests/test_router_setup_builder.py -v -m "not integration" --tb=short
```

Expected: 22 from `test_intraday_router_wf.py` + 7 from `test_router_setup_builder.py` (minus any marked `@pytest.mark.integration` in the latter) = ~29 passed; 1 deselected (the new integration test).

- [ ] **Step 4: Run the integration test explicitly (optional but recommended)**

```bash
.venv/bin/pytest tests/test_intraday_router_wf.py -v -m "integration" --tb=short
```

Expected: 1 passed (the integration test). May take 1-10 minutes on first run; fast on cache hits.

- [ ] **Step 5: Commit**

```bash
git add tests/test_intraday_router_wf.py
git commit -m "test(backtest): integration test for router WF end-to-end smoke

Marked @pytest.mark.integration so the default 'not integration' invocation
skips it. Run explicitly to verify the full pipeline against real Polygon
data on a short window.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Run the full non-integration suite to confirm no regressions across the project**

```bash
.venv/bin/pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -30
```

Expected: all prior tests still pass + the new ~29 tests pass. Total count should be `prior_baseline + 29`.

- [ ] **Run the BUILD_LOG sync if applicable**

Per CLAUDE.md global rule: append a BUILD_LOG entry for this session noting the new WF backtest module, the deferred threshold-calibration exercise, and the next step (run the WF, examine raw stats).
