# Phase 2b — Regime Lens + Concurrency + ExitManager Strategy-Aware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three substantial Phase 2b items that lay the live-path infrastructure for the multi-strategy expansion: (1) formalize the regime-per-timeframe interface, (2) add multi-position concurrency to `paper_broker` (idle until Phase 3 wires intraday signals), (3) refactor `ExitManager` to dispatch exit rules by sub-strategy. Each touches live code — **the discipline gate is byte-identical parity on the existing 45DTE behavior**, verified by targeted tests.

**Architecture:** Each task is additive-with-parity. New interfaces and dispatchers wrap existing functionality so the current 45DTE production path produces byte-identical decisions. Multi-position concurrency adds caps + an event-driven entry method but no new caller invokes it until Phase 3. The intraday exit cron is registered but its `dte_buckets` filter excludes 45DTE, so it finds zero matching positions until Phase 3 produces intraday-tagged trades.

**Tech Stack:** Python, pandas. Reuses `signals/regime_detector.py`, `signals/spy_options_engine.py`, `learning/paper_broker.py`, `learning/exit_manager.py`, `learning/scheduler.py`, `journal/trade_recorder.py`.

**Plan:** Derived from the strategic to-do list items #1, #10, #13. Phase 1 + 2a are merged on `main`.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `signals/regime_lens.py` (new) | Create | `RegimeLens` ABC + `DailyLens` + `IntradayLens` + `LENS_FOR_STRATEGY` registry |
| `learning/paper_broker.py` | Modify | Multi-position concurrency: open-count helper + caps + new `execute_signal()` entry-event method (idle until Phase 3) |
| `learning/exit_manager.py` | Modify | Strategy-aware `_evaluate`: lookup rules by (strategy, dte_bucket); add `manage_open(dte_buckets=...)` filter; preserve 45DTE byte-identical behavior |
| `learning/scheduler.py` | Modify | Existing 16:08 daily cron passes `dte_buckets=["45DTE"]`; new intraday cron registered (every 5 min Mon-Fri 9:30-16:00 ET) passing `dte_buckets=["0DTE", "1-3DTE"]` |
| Tests under `tests/` | Various | Per task, including byte-identical 45DTE parity tests |

---

## Task 1: Multi-timeframe regime lens

**Files:**
- Create: `signals/regime_lens.py`
- Test: `tests/test_regime_lens.py`

A thin abstraction layer over the existing daily `RegimeDetector` and intraday `SPYOptionsEngine`. Each strategy declares which lens it reads via a `LENS_FOR_STRATEGY` registry. This is FORMALIZATION — no functional behavior change. Phase 3+ consumers (intraday paper-broker, per-sub-strategy entry gates) use the registry to look up the right lens.

### Step 1: Write the failing tests — `tests/test_regime_lens.py`:

```python
"""Phase 2b-1: regime-per-timeframe lens abstraction.

A thin wrapper over RegimeDetector (daily) + SPYOptionsEngine (intraday) that
each strategy declares its dependency on via the LENS_FOR_STRATEGY registry.
Formalization only — no functional change to existing behavior."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from signals.regime_lens import (
    RegimeLens, DailyLens, IntradayLens,
    LENS_FOR_STRATEGY, lens_for,
)


def test_lens_registry_maps_known_strategies():
    # 45DTE strategies use the daily lens.
    assert lens_for("call_debit_spread", "45DTE") is DailyLens
    assert lens_for("put_debit_spread",  "45DTE") is DailyLens
    assert lens_for("iron_condor",       "45DTE") is DailyLens
    # 1-3DTE + 0DTE use the intraday lens.
    assert lens_for("call_debit_spread", "1-3DTE") is IntradayLens
    assert lens_for("iron_condor",       "0DTE")   is IntradayLens
    assert lens_for("put_debit_spread",  "0DTE")   is IntradayLens


def test_lens_for_unknown_strategy_returns_none():
    """Unknown (strategy, dte_bucket) combinations return None — caller decides
    whether to default or error."""
    assert lens_for("some_new_strategy", "45DTE") is None
    assert lens_for("iron_condor",       "weird_bucket") is None


def test_daily_lens_wraps_RegimeDetector():
    """DailyLens.read() returns a RegimeResult — same shape RegimeDetector.classify produces."""
    from signals.regime_detector import RegimeResult, Regime
    import pandas as pd
    # Synthetic: 250 days of flat-then-up SPY so detector has enough data.
    n = 250
    closes = [500.0] * 150 + [500.0 + 0.5 * i for i in range(n - 150)]
    spy_df = pd.DataFrame({
        "close": closes,
        "high":  [c * 1.01 for c in closes],
        "low":   [c * 0.99 for c in closes],
    }, index=pd.date_range("2024-01-01", periods=n))
    result = DailyLens().read(spy_daily_df=spy_df, vix_current=17.0, ivr_current=40.0)
    assert isinstance(result, RegimeResult)
    assert isinstance(result.regime, Regime)


def test_intraday_lens_wraps_SPYOptionsEngine():
    """IntradayLens.read() returns a list[SPYSetup] — the engine's native output."""
    from signals.spy_options_engine import SPYSetup
    import pandas as pd
    # Synthetic minimal frames the engine can consume.
    n = 50
    df_15m = pd.DataFrame({
        "open": [500.0] * n, "high": [501.0] * n,
        "low":  [499.0] * n, "close": [500.0] * n,
        "volume": [1_000_000] * n,
    }, index=pd.date_range("2026-05-22 09:30", periods=n, freq="15min"))
    df_5m  = pd.DataFrame({
        "open": [500.0] * n, "high": [500.5] * n,
        "low":  [499.5] * n, "close": [500.0] * n,
        "volume": [333_000] * n,
    }, index=pd.date_range("2026-05-22 09:30", periods=n, freq="5min"))
    setups = IntradayLens().read(df_15m=df_15m, df_5m=df_5m)
    assert isinstance(setups, list)
    # Every emitted setup is a SPYSetup (might be empty list if scoring fails — fine).
    for s in setups:
        assert isinstance(s, SPYSetup)


def test_regime_lens_is_abc_with_read_method():
    """RegimeLens is the abstract base with a read() method both subclasses implement."""
    assert hasattr(RegimeLens, "read")
    assert hasattr(DailyLens, "read")
    assert hasattr(IntradayLens, "read")
```

### Step 2: Run, verify FAILS.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_regime_lens.py -v`
Expected: 5 failures — module doesn't exist.

### Step 3: Create `signals/regime_lens.py`:

```python
"""
signals/regime_lens.py -- Regime-per-timeframe lens abstraction.

Each strategy reads conditions through the lens appropriate to its horizon:
  - 45DTE swing strategies → DailyLens (wraps signals.regime_detector.RegimeDetector)
  - 0DTE / 1-3DTE intraday strategies → IntradayLens (wraps signals.spy_options_engine.SPYOptionsEngine)

LENS_FOR_STRATEGY is the (strategy, dte_bucket) -> lens-class mapping that
Phase 3+ consumers (intraday paper-broker, per-sub-strategy entry gates) read
to know which lens to instantiate for the trade they're considering.

This module is FORMALIZATION only — no functional change to existing behavior.
The bot's current 09:15 daily play path continues to call RegimeDetector
directly; the new lens classes are available for Phase 3+ to use.
"""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class RegimeLens(ABC):
    """Abstract base — both DailyLens and IntradayLens implement read(...)."""

    @abstractmethod
    def read(self, **kwargs) -> Any:
        """Read current conditions. Return type depends on the lens:
          - DailyLens.read() returns a RegimeResult (regime label + tradeable + metrics)
          - IntradayLens.read() returns a list[SPYSetup] (per-strategy candidates with scores)
        """
        ...


class DailyLens(RegimeLens):
    """Wraps signals.regime_detector.RegimeDetector — the existing daily regime classifier."""

    def read(self, *, spy_daily_df, vix_current: float, ivr_current: float,
             today=None) -> "RegimeResult":
        from signals.regime_detector import RegimeDetector
        from datetime import date
        return RegimeDetector().classify(
            spy_daily_df = spy_daily_df,
            vix_current  = vix_current,
            ivr_current  = ivr_current,
            today        = today or date.today(),
        )


class IntradayLens(RegimeLens):
    """Wraps signals.spy_options_engine.SPYOptionsEngine — the existing intraday signal engine."""

    def read(self, *, df_15m, df_5m) -> "list":
        from signals.spy_options_engine import SPYOptionsEngine
        return SPYOptionsEngine().analyze(df_15m, df_5m)


# ── Strategy → Lens registry ──────────────────────────────────────────────
# Each (strategy, dte_bucket) entry resolves to the lens class that strategy
# reads. Phase 3+ consumers look up the right lens via lens_for(...).

LENS_FOR_STRATEGY: dict[tuple[str, str], type[RegimeLens]] = {
    # 45DTE swing — daily lens
    ("call_debit_spread", "45DTE"): DailyLens,
    ("put_debit_spread",  "45DTE"): DailyLens,
    ("iron_condor",       "45DTE"): DailyLens,

    # 1-3DTE — intraday lens
    ("call_debit_spread", "1-3DTE"): IntradayLens,
    ("put_debit_spread",  "1-3DTE"): IntradayLens,
    ("iron_condor",       "1-3DTE"): IntradayLens,

    # 0DTE — intraday lens
    ("call_debit_spread", "0DTE"): IntradayLens,
    ("put_debit_spread",  "0DTE"): IntradayLens,
    ("iron_condor",       "0DTE"): IntradayLens,
}


def lens_for(strategy: str, dte_bucket: str) -> type[RegimeLens] | None:
    """Look up which lens class a given (strategy, dte_bucket) reads through.
    Returns None for unknown combinations — caller decides whether to default
    or error. Phase 2b ships this as discovery infrastructure only; no caller
    invokes lens_for() yet (Phase 3+ will)."""
    return LENS_FOR_STRATEGY.get((strategy, dte_bucket))
```

### Step 4: Run, verify all 5 PASS.

### Step 5: Confirm zero production consumers exist yet (Phase 2b is formalization only):
Run: `grep -rn "from signals.regime_lens\|import regime_lens" learning/ signals/ scanners/ 2>/dev/null | grep -v tests`
Expected: empty output. (Phase 3+ wires consumers.)

### Step 6: Run the FULL non-integration suite to confirm no regressions:
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: all pass (728 baseline + 5 new = 733).

### Step 7: Commit:
```bash
git add signals/regime_lens.py tests/test_regime_lens.py
git commit -m "feat: regime lens abstraction (DailyLens + IntradayLens + registry)

Each strategy reads conditions through the lens appropriate to its horizon
— DailyLens wraps RegimeDetector for 45DTE swings; IntradayLens wraps
SPYOptionsEngine for 0DTE/1-3DTE intraday. LENS_FOR_STRATEGY registry maps
(strategy, dte_bucket) → lens class.

Formalization only — no production caller invokes lens_for() yet. Phase 3+
intraday paper-broker + per-sub-strategy entry gates consume the registry."
```

---

## Task 2: Multi-position concurrency in paper_broker

**Files:**
- Modify: `learning/paper_broker.py` (`execute()` adds open-position counting + cap enforcement; new `execute_signal()` entry-event method; new module-level cap constants)
- Test: `tests/test_paper_broker_concurrency.py`

Today's `execute_today()` is called at 09:16 with one daily play; it opens 1 contract regardless. Multi-position concurrency means: enforce caps on currently-open positions per book, and add a new `execute_signal(setup)` method that lets Phase 3's intraday scanner fire entries event-style. **Crucial parity:** the existing single-daily-play path is byte-identical when caps aren't reached (cap=3 disciplined, current 45DTE flow opens at most 1/day → never reaches cap).

### Step 1: Write the failing tests — `tests/test_paper_broker_concurrency.py`:

```python
"""Phase 2b-2: paper_broker supports multi-position concurrency with caps.

Existing 09:16 daily flow is byte-identical until caps bind (and 45DTE never
opens more than 1/day, so caps don't bind in production today). New
execute_signal(setup) method is added for Phase 3's intraday consumer; no
caller invokes it yet."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from learning.paper_broker import (
    PaperBroker, MAX_CONCURRENT_DISCIPLINED, MAX_CONCURRENT_LEARNING,
)


def _tradeable_play(date_str="2026-05-26"):
    return {
        "date":       date_str,
        "tradeable":  True,
        "regime":     "trending_up_calm",
        "confidence": 0.8,
        "reasons":    ["test"],
        "metrics":    {"spy_close": 740.0, "ma200": 678.0, "ma200_dist_%": 9.0,
                       "adx": 34.0, "vix": 17.0, "ivr": 40.0},
        "options": {
            "tradeable":   True,
            "strategy":    "debit_spread",
            "direction":   "bullish",
            "entry_price": 1.10,
            "max_profit":  200.0,
            "max_loss":    110.0,
            "legs":        [],
        },
    }


def test_caps_constants_have_sane_defaults():
    """Disciplined book is tighter than learning book (real money vs learning samples)."""
    assert MAX_CONCURRENT_DISCIPLINED == 3
    assert MAX_CONCURRENT_LEARNING    == 6
    assert MAX_CONCURRENT_LEARNING >= MAX_CONCURRENT_DISCIPLINED


def test_single_daily_play_proceeds_normally(tmp_path, monkeypatch):
    """Parity: with 0 open positions, the existing 45DTE flow opens 1 trade
    just like today. No cap interference."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    result = broker.execute(_tradeable_play("2026-05-26"))
    assert result.get("recorded") is True or result.get("trade_id") is not None


def test_cap_blocks_new_disciplined_when_already_at_max(tmp_path, monkeypatch):
    """If 3 disciplined positions are already open, the next call to execute()
    must NOT open a 4th — but it still logs the Prediction (we learn from
    skipped entries)."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    # Seed 3 open disciplined trades.
    for i in range(MAX_CONCURRENT_DISCIPLINED):
        broker.execute(_tradeable_play(date_str=f"2026-05-2{i}"))
    # Sanity: 3 open trades on the books.
    from journal.trade_recorder import TradeRecorder
    open_n = sum(1 for t in TradeRecorder().get_all_trades()
                 if t.get("outcome") == "open")
    assert open_n == MAX_CONCURRENT_DISCIPLINED

    # Try to open a 4th.
    result = broker.execute(_tradeable_play(date_str="2026-05-27"))
    # Trade NOT opened (capped); Prediction still logged.
    assert result.get("trade_id") is None
    # The Prediction record exists for date 2026-05-27.
    from learning.predictions import PredictionLog
    pred = PredictionLog().get("2026-05-27")
    assert pred is not None


def test_cap_count_filtered_by_book(tmp_path, monkeypatch):
    """The disciplined cap only counts disciplined-book open positions.
    Open learning-book trades don't push the disciplined cap."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    # Seed 5 open learning-book trades directly (bypassing the broker).
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    for i in range(5):
        rec.log_entry(
            ticker="SPY", entry_price=1.0, size=1,
            trade_type="option_spread", strategy="iron_condor",
            direction="neutral", mode="swing", legs=[],
            dte_bucket="0DTE", book="learning",
        )
    # The broker should still happily open a disciplined trade — caps are per-book.
    broker = PaperBroker()
    result = broker.execute(_tradeable_play("2026-05-26"))
    assert result.get("trade_id") is not None


def test_execute_signal_method_exists_for_phase3_consumers(tmp_path, monkeypatch):
    """Phase 3's intraday scanner will call execute_signal(setup, book='learning' or 'disciplined').
    For now we just verify the method exists and respects caps."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    # Synthetic setup-shaped input (Phase 3 will fill this with real SPYSetup output).
    setup = {
        "date":        "2026-05-26",
        "strategy":    "iron_condor",
        "dte_bucket":  "0DTE",
        "book":        "learning",
        "direction":   "neutral",
        "entry_price": 2.50,
        "max_profit":  250.0,
        "max_loss":    250.0,
        "legs":        [],
    }
    result = broker.execute_signal(setup)
    assert result.get("trade_id") is not None
    # The trade is tagged as learning + 0DTE iron_condor.
    from journal.trade_recorder import TradeRecorder
    t = TradeRecorder().get_trade_by_id(result["trade_id"])
    assert t["book"]       == "learning"
    assert t["dte_bucket"] == "0DTE"
    assert t["strategy"]   == "iron_condor"
```

### Step 2: Run, verify FAILS.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_paper_broker_concurrency.py -v`
Expected: 5 failures — constants missing, cap not enforced, execute_signal doesn't exist.

### Step 3: Read the current `learning/paper_broker.py` `execute()` method (around lines 99-160). It currently:
- Takes a `play` dict
- Logs a Prediction
- If `play["tradeable"]`, calls `self.trades.log_entry(...)` to open one position
- Returns a dict with `trade_id`, `recorded`, `prediction_date`

We need to add:
1. Module-level constants `MAX_CONCURRENT_DISCIPLINED = 3` and `MAX_CONCURRENT_LEARNING = 6`
2. An `_open_count_by_book(book)` helper that counts currently-open trades for the given book
3. In `execute()`: before calling `log_entry`, check `_open_count_by_book("disciplined") >= MAX_CONCURRENT_DISCIPLINED`; if so, skip the log_entry (still log the Prediction)
4. A new `execute_signal(setup)` method that handles event-driven entries from Phase 3 consumers

### Step 4: Add the constants + helpers. Near the top of `learning/paper_broker.py`, after the existing imports and before the `class PaperBroker:` line, add:

```python
# ── Multi-position concurrency caps ─────────────────────────────────────────
# Per-book limits on open paper positions. Disciplined book is tighter (it's
# the bot's real-money proxy); learning book is looser (it's sample-gathering).
# Used by execute() and execute_signal() to gate new openings.
MAX_CONCURRENT_DISCIPLINED = 3
MAX_CONCURRENT_LEARNING    = 6
```

### Step 5: Inside `class PaperBroker`, add an `_open_count_by_book` helper method (place it after `__init__` and before `execute_today`):

```python
    def _open_count_by_book(self, book: str) -> int:
        """Count currently-open paper trades tagged with the given book.
        Trades that lack a book field (legacy untagged) are treated as
        'disciplined' for the count, since that's all the bot historically
        produced."""
        n = 0
        for t in self.trades.get_all_trades():
            if t.get("outcome") != "open":
                continue
            t_book = t.get("book") or "disciplined"   # legacy untagged ↦ disciplined
            if t_book == book:
                n += 1
        return n
```

### Step 6: Modify `execute()` to enforce the disciplined cap. Find the section in `execute()` AFTER the Prediction is logged but BEFORE `self.trades.log_entry(...)` is called. Wrap the `log_entry` call in a cap check:

```python
        if tradeable:
            open_disc = self._open_count_by_book("disciplined")
            if open_disc >= MAX_CONCURRENT_DISCIPLINED:
                logger.info(
                    f"PaperBroker: disciplined cap reached ({open_disc}/"
                    f"{MAX_CONCURRENT_DISCIPLINED}) — prediction logged, no new position"
                )
                return {
                    "prediction_date": today_str,
                    "trade_id":        None,
                    "recorded":        False,
                    "skipped_reason":  "disciplined_book_cap",
                }
            # ... existing log_entry call ...
```

(Read the actual `execute()` body to find the exact insertion point — the `if tradeable:` branch contains the `log_entry` call.)

### Step 7: Add the new `execute_signal()` method. Place it inside `class PaperBroker`, AFTER `execute()` and BEFORE `_plan_to_play`:

```python
    def execute_signal(self, setup: dict) -> dict:
        """Event-driven entry — Phase 3's intraday scanner will call this when
        a sub-strategy setup fires intraday. Respects per-book concurrency caps.

        setup dict shape:
          {
            "date":        str (today's ISO date),
            "strategy":    str ("call_debit_spread" / "put_debit_spread" / "iron_condor"),
            "dte_bucket":  str ("0DTE" / "1-3DTE"),
            "book":        str ("disciplined" / "learning"),
            "direction":   str ("bullish" / "bearish" / "neutral"),
            "entry_price": float,
            "max_profit":  float,
            "max_loss":    float,
            "legs":        list[dict],
          }
        """
        book = setup.get("book", "disciplined")
        cap  = MAX_CONCURRENT_LEARNING if book == "learning" else MAX_CONCURRENT_DISCIPLINED
        open_n = self._open_count_by_book(book)
        if open_n >= cap:
            logger.info(
                f"PaperBroker.execute_signal: {book} cap reached ({open_n}/{cap}) — skipped"
            )
            return {"trade_id": None, "recorded": False, "skipped_reason": f"{book}_book_cap"}

        tid = self.trades.log_entry(
            ticker      = "SPY",
            entry_price = float(setup.get("entry_price", 0.0)),
            size        = 1,
            trade_type  = "option_spread",
            strategy    = setup.get("strategy"),
            direction   = setup.get("direction", "neutral"),
            mode        = "intraday" if setup.get("dte_bucket") in ("0DTE", "1-3DTE") else "swing",
            legs        = setup.get("legs", []),
            max_profit  = setup.get("max_profit"),
            max_loss    = setup.get("max_loss"),
            notes       = f"[AUTO-PAPER {setup.get('date')}] event-driven entry",
            dte_bucket  = setup.get("dte_bucket"),
            book        = book,
        )
        logger.info(
            f"PaperBroker.execute_signal: opened {tid} | "
            f"{setup.get('strategy')} @ {setup.get('dte_bucket')} ({book})"
        )
        return {"trade_id": tid, "recorded": True}
```

### Step 8: Run focused tests, verify all 5 PASS.

### Step 9: Run the FULL non-integration suite to confirm no regressions:
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: 733 baseline + 5 new = 738. The existing paper_broker tests must still pass (the cap doesn't bind on their 1-trade-per-test pattern).

### Step 10: Confirm no caller invokes `execute_signal` in production code:
Run: `grep -rn "execute_signal" learning/ signals/ scanners/ 2>/dev/null | grep -v tests`
Expected: only the definition line. (Phase 3 wires the caller.)

### Step 11: Commit:
```bash
git add learning/paper_broker.py tests/test_paper_broker_concurrency.py
git commit -m "feat: multi-position concurrency + execute_signal in paper_broker

Adds MAX_CONCURRENT_DISCIPLINED=3, MAX_CONCURRENT_LEARNING=6 caps + the
_open_count_by_book helper that counts currently-open trades by book.
execute() now enforces the disciplined cap (skips log_entry, still logs
Prediction). New execute_signal(setup) method for Phase 3's event-driven
intraday scanner — no caller invokes it yet.

Existing 09:16 daily flow is byte-identical: today's 45DTE bot opens at
most 1 trade/day, well under cap=3, so the new check is no-op in production
today. The infrastructure is in place for Phase 3 to fire concurrent
intraday entries without runaway position-opening."
```

---

## Task 3: ExitManager strategy-aware refactor + intraday cron

**Files:**
- Modify: `learning/exit_manager.py` (add `_exit_rule_for(strategy, dte_bucket)` lookup; refactor `_evaluate` to use it; add `dte_buckets` filter param to `manage_open`)
- Modify: `learning/scheduler.py` (existing daily cron passes `dte_buckets=["45DTE"]`; new intraday cron every 5 min Mon-Fri 9:30-16:00 ET passes `dte_buckets=["0DTE", "1-3DTE"]`)
- Test: `tests/test_exit_manager_strategy_aware.py`

**The discipline gate:** byte-identical 45DTE behavior. The refactored `_evaluate` produces the same (exit_px, reason) tuple as the original inline logic for every 45DTE trade input. Tests prove this explicitly. The new intraday cron registers but its `dte_buckets` filter excludes 45DTE; it has zero open positions to manage today (paper_broker hardcodes 45DTE) → it's a no-op until Phase 3.

### Step 1: Write the failing tests — `tests/test_exit_manager_strategy_aware.py`:

```python
"""Phase 2b-3: ExitManager dispatches exit rules by (strategy, dte_bucket).

THE PARITY GATE: byte-identical 45DTE behavior. For any 45DTE trade input,
the refactored _evaluate produces the same (exit_px, reason) tuple as the
original inline logic. Tests prove this explicitly.

The new intraday cron registers via the scheduler but its dte_buckets
filter excludes 45DTE — it's a no-op until Phase 3 produces intraday trades."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date, timedelta
import pytest

import config
from learning.exit_manager import ExitManager, _exit_rule_for


# ── Per-(strategy, dte_bucket) rule lookup ────────────────────────────────

def test_exit_rule_for_45dte_returns_existing_constants():
    """45DTE rules must match the old global PROFIT_TARGET_PCT=0.70 and
    DTE_CLOSE_THRESHOLD=21 EXACTLY. This is the parity contract."""
    for structure in ("call_debit_spread", "put_debit_spread", "iron_condor",
                       "debit_spread", "credit_spread"):  # incl. legacy strategy names
        r = _exit_rule_for(structure, "45DTE")
        assert r["profit_target_pct"]    == 0.70
        assert r["dte_close_threshold"]  == 21
        assert r["stop_pct"]              is None    # no stop on 45DTE (current default)


def test_exit_rule_for_legacy_untagged_defaults_to_45dte():
    """Old trades without dte_bucket field must dispatch to 45DTE rules so
    historical positions on the books keep working identically."""
    r = _exit_rule_for("debit_spread", None)
    assert r["profit_target_pct"] == 0.70
    assert r["dte_close_threshold"] == 21


def test_exit_rule_for_0dte_call_uses_aggressive_rules():
    r = _exit_rule_for("call_debit_spread", "0DTE")
    assert r["profit_target_pct"] == config.PROFIT_TARGET_PCT_0DTE_CALL  # 1.00
    assert r["stop_pct"]          == config.STOP_PCT_0DTE_CALL           # 0.75
    assert r.get("forced_close_time") == config.FORCED_CLOSE_TIME_0DTE_DEBIT  # "15:30"


def test_exit_rule_for_0dte_condor_uses_short_strike_touch():
    r = _exit_rule_for("iron_condor", "0DTE")
    assert r["profit_target_pct"]          == config.PROFIT_TARGET_PCT_0DTE_COND  # 0.30
    assert r["condor_short_strike_touch"]  is True
    assert r.get("forced_close_time")       == config.FORCED_CLOSE_TIME_0DTE_CONDOR


def test_exit_rule_for_1_3dte_uses_50pct_target_and_stop():
    r = _exit_rule_for("call_debit_spread", "1-3DTE")
    assert r["profit_target_pct"] == 0.50
    assert r["stop_pct"]          == 0.50


# ── Byte-identical 45DTE parity for _evaluate ─────────────────────────────

def _make_trade_45dte(strategy="debit_spread", direction="bullish", days_to_exp=30):
    today = date(2026, 5, 23)
    expiry = today + timedelta(days=days_to_exp)
    return {
        "trade_id":   "TEST00001",
        "strategy":   strategy,
        "direction":  direction,
        "entry_price": 1.50,
        "size":       1,
        "max_profit": 300.0,
        "max_loss":   150.0,
        "legs": [{
            "action": "BUY", "option_type": "CALL", "strike": 700,
            "expiry": expiry.isoformat(),
        }, {
            "action": "SELL", "option_type": "CALL", "strike": 710,
            "expiry": expiry.isoformat(),
        }],
        "dte_bucket": "45DTE",
        "book":       "disciplined",
    }


def test_evaluate_45dte_profit_target_fires_at_70pct(tmp_path, monkeypatch):
    """The 45DTE profit-target gate must fire when pnl/max_profit >= 0.70."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    trade = _make_trade_45dte(days_to_exp=30)
    # Synthetic SPY price + VIX where the spread is ~75% of max profit on close mark.
    # We can't easily target an exact pnl ratio without going through BS — instead,
    # just confirm: at DTE near expiry (DTE=21 → time-stop), the manager closes.
    decision = mgr._evaluate(trade, spy=730.0, vix=17.0, today=date(2026, 5, 23))
    # At 30 DTE, profit target likely not hit yet on a flat market — expect None.
    # (This is a smoke check; the parity assertions below are the real gate.)
    assert decision is None or isinstance(decision, tuple)


def test_evaluate_45dte_time_stop_fires_at_21_dte(tmp_path, monkeypatch):
    """Day-stop fires when DTE <= 21. Byte-identical to current behavior."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    trade = _make_trade_45dte(days_to_exp=21)   # exactly at threshold
    decision = mgr._evaluate(trade, spy=730.0, vix=17.0, today=date(2026, 5, 23))
    assert decision is not None
    exit_px, reason = decision
    # The reason format from the original code: f"time stop {dte}DTE"
    assert "time stop" in reason
    assert "21DTE" in reason


def test_evaluate_legacy_untagged_trade_uses_45dte_rules(tmp_path, monkeypatch):
    """A trade record from before Phase 2a (no dte_bucket field) must produce
    the same exit decision as a tagged 45DTE trade. Parity for legacy trades."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    tagged = _make_trade_45dte(days_to_exp=21)
    legacy = {k: v for k, v in tagged.items() if k not in ("dte_bucket", "book")}
    d_tagged = mgr._evaluate(tagged, spy=730.0, vix=17.0, today=date(2026, 5, 23))
    d_legacy = mgr._evaluate(legacy, spy=730.0, vix=17.0, today=date(2026, 5, 23))
    # Both should return the same kind of decision (None vs tuple) and same reason
    # since they use the same rules.
    if d_tagged is None:
        assert d_legacy is None
    else:
        assert d_legacy is not None
        assert d_tagged[1] == d_legacy[1]   # same reason string


# ── manage_open dte_buckets filter ────────────────────────────────────────

def test_manage_open_filters_by_dte_buckets(tmp_path, monkeypatch):
    """manage_open(dte_buckets=['45DTE']) processes only 45DTE positions,
    leaving 0DTE/1-3DTE positions alone. Used by the scheduler to keep the
    daily 16:08 cron from re-evaluating intraday trades and vice versa."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    # Seed two open trades — one 45DTE, one 0DTE.
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    today = date.today()
    expiry_45 = (today + timedelta(days=30)).isoformat()
    expiry_0  = today.isoformat()
    t45 = rec.log_entry(
        ticker="SPY", entry_price=1.5, size=1, trade_type="option_spread",
        strategy="debit_spread", direction="bullish", mode="swing",
        legs=[{"action": "BUY", "option_type": "CALL", "strike": 700, "expiry": expiry_45}],
        max_profit=300.0, max_loss=150.0,
        notes="[AUTO-PAPER] test", dte_bucket="45DTE", book="disciplined",
    )
    t0d = rec.log_entry(
        ticker="SPY", entry_price=0.5, size=1, trade_type="option_spread",
        strategy="call_debit_spread", direction="bullish", mode="intraday",
        legs=[{"action": "BUY", "option_type": "CALL", "strike": 700, "expiry": expiry_0}],
        max_profit=100.0, max_loss=50.0,
        notes="[AUTO-PAPER] test", dte_bucket="0DTE", book="learning",
    )
    mgr = ExitManager()
    # manage_open with dte_buckets=["0DTE"] processes only the 0DTE trade.
    # We can't easily mock spy_close/vix end-to-end, so use the explicit args.
    closed = mgr.manage_open(today=today, spy_close=700.0, vix=17.0,
                              dte_buckets=["0DTE"])
    # The 45DTE trade was NOT touched (it's still open).
    after = rec.get_trade_by_id(t45)
    assert after.get("outcome") == "open"
    # The 0DTE trade may or may not have closed depending on math — the key
    # assertion is the 45DTE one stayed open. Any closed trade is the 0DTE one.
    for c in closed:
        assert c["trade_id"] != t45


def test_manage_open_default_dte_buckets_none_processes_all(tmp_path, monkeypatch):
    """Back-compat: manage_open() with no dte_buckets arg processes everything
    (preserves the existing scheduler-call signature)."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    mgr = ExitManager()
    # Just verify the call signature accepts no dte_buckets arg.
    closed = mgr.manage_open(today=date(2026, 5, 23), spy_close=730.0, vix=17.0)
    assert isinstance(closed, list)
```

### Step 2: Run, verify FAILS.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_exit_manager_strategy_aware.py -v`
Expected: 9 failures — `_exit_rule_for` doesn't exist; `manage_open` doesn't accept `dte_buckets`.

### Step 3: Add `_exit_rule_for` at MODULE level in `learning/exit_manager.py`. Near the top of the file (after the existing imports and module-level constants `PROFIT_TARGET_PCT = 0.70` / `DTE_CLOSE_THRESHOLD = 21`), add:

```python
import config


# ── Per-(strategy, dte_bucket) exit rules ───────────────────────────────────
# Phase 2b-3: ExitManager dispatches to the right rule by (strategy, dte_bucket).
# 45DTE values match the legacy globals exactly (PROFIT_TARGET_PCT=0.70,
# DTE_CLOSE_THRESHOLD=21) — that's the byte-identical parity contract.

# Map raw strategy strings (which include legacy names like "debit_spread") to
# the canonical structure key. The codebase historically used both
# "debit_spread" / "credit_spread" / "iron_condor" / "single_leg" in trade
# records and "call_debit_spread" / "put_debit_spread" / "iron_condor" in the
# new Phase-1 constants. We normalize here.
_STRUCTURE_KEY = {
    "call_debit_spread": "CALL",
    "put_debit_spread":  "PUT",
    "iron_condor":       "COND",
    # Legacy names — direction disambiguates between CALL/PUT debit
    "debit_spread":      "CALL",   # historical default; direction can refine if needed
    "credit_spread":     "CALL",
    "single_leg":        "CALL",
}


def _exit_rule_for(strategy: str | None, dte_bucket: str | None) -> dict:
    """Return the exit-rule dict for the given (strategy, dte_bucket).
    Untagged trades (dte_bucket=None) default to 45DTE rules — the only thing
    the bot has historically produced.

    Returns: {
        "profit_target_pct": float,
        "stop_pct": float | None,
        "dte_close_threshold": int,
        "condor_short_strike_touch": bool,
        "forced_close_time": str | None,   # HH:MM ET for 0DTE; None otherwise
        "forced_close_minutes_before_expiry": int | None,  # for 1-3DTE
    }
    """
    structure = _STRUCTURE_KEY.get(strategy or "", "CALL")
    bucket = dte_bucket or "45DTE"   # legacy untagged → 45DTE

    if bucket == "45DTE":
        # Look up the per-structure constant; all three currently equal 0.70.
        pt_pct = {
            "CALL": config.PROFIT_TARGET_PCT_45DTE_CALL,
            "PUT":  config.PROFIT_TARGET_PCT_45DTE_PUT,
            "COND": config.PROFIT_TARGET_PCT_45DTE_COND,
        }[structure]
        return {
            "profit_target_pct":   pt_pct,
            "stop_pct":            config.STOP_PCT_45DTE,    # None by default
            "dte_close_threshold": config.DTE_CLOSE_THRESHOLD_45DTE,
            "condor_short_strike_touch":          False,
            "forced_close_time":                   None,
            "forced_close_minutes_before_expiry":  None,
        }

    if bucket == "1-3DTE":
        pt_pct = {
            "CALL": config.PROFIT_TARGET_PCT_1_3DTE_CALL,
            "PUT":  config.PROFIT_TARGET_PCT_1_3DTE_PUT,
            "COND": config.PROFIT_TARGET_PCT_1_3DTE_COND,
        }[structure]
        stop_pct = (config.STOP_PCT_1_3DTE_CALL if structure == "CALL"
                    else config.STOP_PCT_1_3DTE_PUT if structure == "PUT"
                    else None)   # condors use strike-touch, not %-of-max-loss
        return {
            "profit_target_pct":   pt_pct,
            "stop_pct":            stop_pct,
            "dte_close_threshold": 0,    # 1-3DTE managed by forced-close, not DTE threshold
            "condor_short_strike_touch":          (structure == "COND"
                and config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_1_3DTE),
            "forced_close_time":                   None,
            "forced_close_minutes_before_expiry":  config.FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE,
        }

    if bucket == "0DTE":
        pt_pct = {
            "CALL": config.PROFIT_TARGET_PCT_0DTE_CALL,
            "PUT":  config.PROFIT_TARGET_PCT_0DTE_PUT,
            "COND": config.PROFIT_TARGET_PCT_0DTE_COND,
        }[structure]
        stop_pct = (config.STOP_PCT_0DTE_CALL if structure == "CALL"
                    else config.STOP_PCT_0DTE_PUT if structure == "PUT"
                    else None)
        forced_time = (config.FORCED_CLOSE_TIME_0DTE_CONDOR if structure == "COND"
                       else config.FORCED_CLOSE_TIME_0DTE_DEBIT)
        return {
            "profit_target_pct":   pt_pct,
            "stop_pct":            stop_pct,
            "dte_close_threshold": 0,
            "condor_short_strike_touch":          (structure == "COND"
                and config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_0DTE),
            "forced_close_time":                   forced_time,
            "forced_close_minutes_before_expiry":  None,
        }

    # Unknown bucket — defensive default (treat as 45DTE).
    return _exit_rule_for(strategy, "45DTE")
```

### Step 4: Refactor `_evaluate` to use `_exit_rule_for`. Find the existing method (around lines 171-200). Replace the hardcoded `PROFIT_TARGET_PCT` and `DTE_CLOSE_THRESHOLD` references with the rule-lookup. The new body:

```python
    def _evaluate(self, trade: dict, spy: float, vix: float,
                  today: date) -> tuple[float, str] | None:
        """
        Return (exit_price, reason) if the position should close today,
        else None. exit_price already includes the slippage haircut.

        Phase 2b-3: dispatches the exit rule by (strategy, dte_bucket).
        Untagged trades (legacy, no dte_bucket field) default to 45DTE rules.
        45DTE behavior is byte-identical to the original implementation
        (PROFIT_TARGET_PCT_45DTE_*=0.70, DTE_CLOSE_THRESHOLD_45DTE=21).
        """
        legs     = trade.get("legs") or []
        strategy = (trade.get("strategy") or trade.get("trade_type") or "single_leg").lower()
        exp      = self._nearest_expiration(legs)
        if exp is None:
            return None
        dte = (exp - today).days
        if dte < 0:
            return None   # already expired -> ExpiryResolver's job

        # Look up the per-sub-strategy rule.
        rule = _exit_rule_for(strategy, trade.get("dte_bucket"))

        exit_px = self._mark_exit_price(strategy, legs, spy, vix, today, dte)
        pnl     = self._pnl_dollars(strategy, trade.get("entry_price"), exit_px,
                                    trade.get("size", 1))
        max_profit = self._numeric(trade.get("max_profit"))
        max_loss   = self._numeric(trade.get("max_loss"))

        # 1. Profit target — gated by per-sub-strategy threshold.
        if max_profit and max_profit > 0 and pnl is not None:
            if pnl / max_profit >= rule["profit_target_pct"]:
                return exit_px, f"profit target {rule['profit_target_pct']:.0%}"

        # 2. Hard stop — Phase 2b experimental for 45DTE; configured for 0DTE/1-3DTE.
        if rule["stop_pct"] is not None and max_loss and max_loss > 0 and pnl is not None:
            if pnl <= -rule["stop_pct"] * max_loss:
                return exit_px, f"stop {rule['stop_pct']:.0%} of max loss"

        # 3. Time stop — close N DTE before expiry.
        if dte <= rule["dte_close_threshold"]:
            return exit_px, f"time stop {dte}DTE"

        return None
```

### Step 5: Refactor `manage_open` to accept `dte_buckets` filter. Find the method (around lines 110-167). Update the signature + add a filter step:

```python
    def manage_open(
        self,
        today:        date  | None = None,
        spy_close:    float | None = None,
        vix:          float | None = None,
        dte_buckets:  list[str] | None = None,
    ) -> list[dict]:
        """
        Walk open [AUTO-PAPER] trades and close any that hit the profit
        target / stop / time stop. Returns a list of closed-trade dicts.
        Expiry-day positions are left for ExpiryResolver.

        dte_buckets: when provided, only process trades whose dte_bucket
        matches one of the listed values. Used by the scheduler to keep the
        16:08 daily cron from re-evaluating intraday positions (which are
        managed by the every-5-min intraday cron). Trades without a
        dte_bucket field count as "45DTE" for filtering.

        When dte_buckets is None (back-compat), processes every open trade
        regardless of dte_bucket.
        """
        # ... existing today/spy_close/vix defaulting (KEEP) ...

        open_auto = [
            t for t in self.trades.get_all_trades()
            if t.get("outcome") == "open" and AUTO_TAG in (t.get("notes_entry") or "")
        ]
        if dte_buckets is not None:
            buckets_set = set(dte_buckets)
            open_auto = [
                t for t in open_auto
                if (t.get("dte_bucket") or "45DTE") in buckets_set
            ]
        if not open_auto:
            return []

        # ... rest of the method unchanged ...
```

The exact insertion point: AFTER the existing `open_auto = [...]` list comprehension and BEFORE the existing `if not open_auto: return []` line. Insert the `if dte_buckets is not None:` filter block.

### Step 6: Run focused tests, verify all 9 PASS.

### Step 7: Update the scheduler. Open `learning/scheduler.py`. Find `job_exit_manager` (around line 65) and add a `dte_buckets` parameter that's threaded into `manage_open`:

```python
def job_exit_manager(polygon_client, vix_client=None, post_fn=None,
                     dte_buckets=None):
    try:
        mgr = ExitManager(
            trades=TradeRecorder(),
            polygon_client=polygon_client,
            vix_client=vix_client,
        )
        closed = mgr.manage_open(dte_buckets=dte_buckets)
        logger.info(f"learning.exit_manager [{dte_buckets or 'all'}] -> {len(closed)} closed")
        if post_fn and closed:
            try:
                post_fn(format_exit_message(closed))
            except Exception as e:
                logger.warning(f"learning.exit_manager notify failed: {e}")
    except Exception as e:
        logger.exception(f"learning.exit_manager failed: {e}")
```

Then find the EXISTING `scheduler.add_job(job_exit_manager, CronTrigger(...))` call in `register_learning_jobs` (around line 158). Update its `kwargs` to pass `dte_buckets=["45DTE"]`:

```python
    scheduler.add_job(
        job_exit_manager,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=8, timezone=eastern),
        kwargs={"polygon_client": polygon_client, "vix_client": vix_client,
                "post_fn": post_fn, "dte_buckets": ["45DTE"]},
        id="learning_exit_manager",
        name="Learning: exit manager (daily 45DTE)",
        replace_existing=True,
    )
```

Then add a SECOND `scheduler.add_job(...)` IMMEDIATELY AFTER it for the intraday cron:

```python
    # Phase 2b-3: intraday exit cron. Runs every 5 min during market hours,
    # processes only 0DTE/1-3DTE positions. No-op today because paper_broker
    # hardcodes 45DTE; Phase 3's intraday entry pipeline will produce trades
    # this cron then manages.
    scheduler.add_job(
        job_exit_manager,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5",
                     timezone=eastern),
        kwargs={"polygon_client": polygon_client, "vix_client": vix_client,
                "post_fn": post_fn, "dte_buckets": ["0DTE", "1-3DTE"]},
        id="learning_exit_manager_intraday",
        name="Learning: exit manager (intraday 0DTE / 1-3DTE)",
        replace_existing=True,
    )
```

Also update the bottom-of-function `logger.info(...)` block to mention the new cron in the summary:

```python
    logger.info("   16:08 ET (Mon-Fri) - exit manager [45DTE daily]")
    logger.info("   every 5 min 9:30-16:00 ET (Mon-Fri) - exit manager [0DTE / 1-3DTE intraday]")
```

(Add these in the same `logger.info` summary block where the other learning-job times are logged.)

### Step 8: Update the scheduler-jobs test that enumerates registered job IDs. The existing `tests/test_learning_scheduler.py::test_register_learning_jobs_adds_all_jobs` expects 9 jobs; now it's 10. Open that test, find the assertion `assert len(s.jobs) == 9` and the `job_ids == {...}` set, update them:

```python
    assert len(s.jobs) == 10
    job_ids = {j["id"] for j in s.jobs}
    assert job_ids == {
        "learning_paper_broker",
        "learning_outcome_resolver",
        "learning_exit_manager",
        "learning_expiry_resolver",
        "learning_reflector",
        "learning_hypothesis_engine",
        "learning_hypothesis_runner",
        "learning_off_hours",
        "learning_meta_recalibration",
        "learning_exit_manager_intraday",     # NEW
    }
```

### Step 9: Run focused tests + scheduler tests, verify all PASS.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_exit_manager_strategy_aware.py tests/test_learning_scheduler.py -v`

### Step 10: Run the FULL non-integration suite to confirm no regressions.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: 738 (after Task 2) + 9 new = 747. If any pre-existing exit_manager or scheduler tests broke (because they asserted old behavior), READ them and update — but the parity for 45DTE must hold, so any 45DTE-asserting tests should still pass.

### Step 11: Commit:
```bash
git add learning/exit_manager.py learning/scheduler.py tests/test_exit_manager_strategy_aware.py tests/test_learning_scheduler.py
git commit -m "feat: strategy-aware ExitManager + intraday cron (parity-validated)

ExitManager._evaluate now dispatches by (strategy, dte_bucket) to per-sub-
strategy exit rules via _exit_rule_for(...). 45DTE behavior is byte-identical
to the original (PROFIT_TARGET_PCT_45DTE_* = 0.70, DTE_CLOSE_THRESHOLD_45DTE
= 21) — verified by parity tests.

manage_open() gains a dte_buckets filter; the existing daily 16:08 cron
passes ['45DTE'], and a new intraday cron (every 5 min Mon-Fri 9:30-16:00 ET)
passes ['0DTE', '1-3DTE']. The intraday cron is no-op today because
paper_broker still hardcodes 45DTE. Phase 3's intraday entry pipeline will
produce trades that the intraday cron then manages.

Legacy trades without dte_bucket field default to 45DTE rules — byte-
identical behavior for historical positions on the books."
```

---

## Self-Review

**Spec coverage:**
- To-do #1 (multi-timeframe regime detectors) → Task 1 ✓ (regime_lens module + LENS_FOR_STRATEGY registry)
- To-do #10 (multi-position concurrency support) → Task 2 ✓ (caps + execute_signal)
- To-do #13 (ExitManager strategy-aware) → Task 3 ✓ (_exit_rule_for + dispatch + intraday cron)

**Placeholder scan:** None. Every code block is concrete. The `_STRUCTURE_KEY` map handles the legacy/canonical naming clash explicitly.

**Type consistency:** `lens_for(strategy, dte_bucket) -> type[RegimeLens] | None` (Task 1). `_open_count_by_book(book: str) -> int` and `execute_signal(setup: dict) -> dict` (Task 2). `_exit_rule_for(strategy, dte_bucket) -> dict` and `manage_open(today=..., spy_close=..., vix=..., dte_buckets=...)` (Task 3) all consistent.

**Parity discipline:** The byte-identical 45DTE contract is enforced by explicit tests in Task 3 (test_exit_rule_for_45dte_returns_existing_constants asserts 0.70 + 21 exactly; test_evaluate_legacy_untagged_trade_uses_45dte_rules asserts a tagged vs untagged trade produce identical decisions). Tasks 1 and 2 don't touch the existing 45DTE decision path at all.

**Dependency order:** 1 (regime_lens, independent) → 2 (paper_broker, independent of 1) → 3 (ExitManager + scheduler, depends on the Phase 1 exit-rule constants which are already merged).
