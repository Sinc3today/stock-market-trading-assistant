# Phase 3 — Intraday Entry Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `intraday_scanner`'s existing high-conviction setups into `paper_broker.execute_signal` (built Phase 2b, no caller yet) so they become real disciplined-book paper trades — applying H2 DTE assignment, Option D dedup, and a default-ON kill-switch flag.

**Architecture:** A new pure-function `intraday_entry_router` module composes H2 (time-of-day DTE + Friday safeguard + ultra-conv doubling) and D (one open per combo, ≤2/day per combo) rules. The intraday scanner calls the router inline after each setup's alert. A new `_entry_count_today_by_combo` helper on `paper_broker` provides the per-day count the router needs. Feature flag `INTRADAY_PAPER_BROKER_ENABLED` (default True) gates the new wiring.

**Tech Stack:** Python, pandas, pytz. Reuses `signals/spy_options_engine.py` (SPYSetup), `learning/paper_broker.py` (execute_signal from Phase 2b), `journal/trade_recorder.py` (get_trades_by from Phase 2a).

**Spec:** `docs/superpowers/specs/2026-05-23-phase3-intraday-entry-pipeline-design.md`

**Honest scope note:** Phase 3 wires existing signals to existing trade-opening infrastructure. P&L on intraday trades will use placeholder pricing (entry_price=1.0, max_profit=200, max_loss=100) — Phase 3 tests whether the wiring + rules work as designed, not whether trade P&L is accurate. Phase 4 designs the real per-sub-strategy structure builder.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `config.py` | Modify | Add 5 Phase-3 constants (flag, tier minimum, DTE cutoff, ultra-conv threshold, per-day cap) |
| `learning/paper_broker.py` | Modify | Add `_entry_count_today_by_combo(strategy, dte_bucket)` helper |
| `signals/intraday_entry_router.py` | Create | Pure-function router: `route(setup, now, trade_recorder, paper_broker) -> list[dict]` |
| `scanners/intraday_scanner.py` | Modify | After each setup's alert posts, call router → maybe execute_signal (flag-gated) |
| Tests | Various | Per task |

---

## Task 1: Phase 3 config constants

**Files:**
- Modify: `config.py` (add after the existing `PER-SUB-STRATEGY EXIT RULES` block from Phase 1)
- Test: `tests/test_phase3_config.py`

Pure data — nothing consumes them until later tasks. Adding them now means the router (Task 2) has stable names to import.

- [ ] **Step 1: Write the failing test — `tests/test_phase3_config.py`:**

```python
"""Phase 3: intraday entry pipeline constants are declared in config.py.

Pure data; consumers land in Task 2+."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_kill_switch_defaults_on():
    """First phase with live behavior change — flag exists, default True
    (changes ship on merge, kill-switch available for emergency revert)."""
    assert config.INTRADAY_PAPER_BROKER_ENABLED is True


def test_entry_tier_minimum_is_high():
    assert config.ENTRY_TIER_MINIMUM == "high"


def test_dte_morning_cutoff_is_1230_et():
    assert config.INTRADAY_DTE_MORNING_CUTOFF == "12:30"


def test_ultra_conviction_threshold_is_85():
    assert config.ULTRA_CONVICTION_DOUBLE_DTE_SCORE == 85


def test_per_combo_daily_cap_is_two():
    assert config.INTRADAY_PER_COMBO_DAILY_CAP == 2
```

- [ ] **Step 2: Run, verify FAILS (5 AttributeError failures).**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_phase3_config.py -v`

- [ ] **Step 3: Add the constants to `config.py`.**

Find the existing `PER-SUB-STRATEGY EXIT RULES` block (last line is `FORCED_CLOSE_TIME_0DTE_CONDOR = "15:00"` or similar). Immediately AFTER that block, add:

```python


# ─────────────────────────────────────────
# PHASE 3: INTRADAY ENTRY PIPELINE
# ─────────────────────────────────────────
# Wires intraday_scanner's high-conviction setups → paper_broker.execute_signal.
# Kill-switch for the intraday-scanner → paper_broker wiring. Default True
# (Phase 3's behavior change ships ON at merge); flip to False + commit to
# instantly disable the pipeline without untangling code.
INTRADAY_PAPER_BROKER_ENABLED = True

# Which conviction tier qualifies as an intraday entry. Configurable so we
# can widen later to include "standard" (45-67 score) without code change.
ENTRY_TIER_MINIMUM = "high"   # one of "high" / "standard"

# H2 DTE assignment: morning (< this ET time) → 0DTE; afternoon → 1-3DTE.
# Friday PM safeguard fires in the router regardless (no weekend exposure).
INTRADAY_DTE_MORNING_CUTOFF = "12:30"

# Ultra-conviction exception: setups with score ≥ this open BOTH 0DTE and
# 1-3DTE buckets (rare — empirically 1-2/week on high-conv setups).
ULTRA_CONVICTION_DOUBLE_DTE_SCORE = 85

# Option D position dedup: max entries per (strategy, dte_bucket) per day.
# After a position closes, a fresh setup can re-open up to this cap.
INTRADAY_PER_COMBO_DAILY_CAP = 2
```

- [ ] **Step 4: Run, verify all 5 PASS.**

- [ ] **Step 5: Run the FULL non-integration suite to confirm no regressions:**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: 748 baseline + 5 new = 753.

- [ ] **Step 6: Commit:**

```bash
git add config.py tests/test_phase3_config.py
git commit -m "feat: Phase 3 intraday entry pipeline constants (flag default ON)"
```

---

## Task 2: `paper_broker._entry_count_today_by_combo` helper

**Files:**
- Modify: `learning/paper_broker.py` (add a new helper next to `_open_count_by_book` from Phase 2b)
- Test: `tests/test_paper_broker_entry_count_today.py`

The router needs to know "how many times has this (strategy, dte_bucket) combo opened today?" to enforce the per-day cap. The cleanest place for this is on `PaperBroker` next to the existing `_open_count_by_book` helper.

- [ ] **Step 1: Write the failing tests — `tests/test_paper_broker_entry_count_today.py`:**

```python
"""Phase 3: PaperBroker._entry_count_today_by_combo counts today's opens
per (strategy, dte_bucket). Restart-safe (reads from persistent TradeRecorder)."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
import pytz

from learning.paper_broker import PaperBroker
from journal.trade_recorder import TradeRecorder

EASTERN = pytz.timezone("US/Eastern")


def _seed_trade(rec, strategy, dte_bucket, book="disciplined"):
    """Helper: open one trade with the given combo."""
    return rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="option_spread", strategy=strategy,
        direction="bullish", mode="intraday", legs=[],
        max_profit=200.0, max_loss=100.0,
        notes="[AUTO-PAPER] test", dte_bucket=dte_bucket, book=book,
    )


def test_entry_count_zero_for_combo_never_opened(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    assert broker._entry_count_today_by_combo("call_debit_spread", "0DTE") == 0


def test_entry_count_counts_today_opens(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    _seed_trade(rec, "call_debit_spread", "0DTE")
    _seed_trade(rec, "call_debit_spread", "0DTE")
    # Same combo, different combo, different strategy — only the same combo counts.
    _seed_trade(rec, "call_debit_spread", "1-3DTE")
    _seed_trade(rec, "iron_condor",       "0DTE")
    assert broker._entry_count_today_by_combo("call_debit_spread", "0DTE") == 2
    assert broker._entry_count_today_by_combo("call_debit_spread", "1-3DTE") == 1
    assert broker._entry_count_today_by_combo("iron_condor", "0DTE") == 1


def test_entry_count_uses_ET_date(tmp_path, monkeypatch):
    """'Today' is today in US/Eastern (matches the rest of the bot's market-hours logic)."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    _seed_trade(rec, "iron_condor", "0DTE")
    # A trade just opened — should count as "today" regardless of local timezone quirks.
    n = broker._entry_count_today_by_combo("iron_condor", "0DTE")
    assert n == 1
```

- [ ] **Step 2: Run, verify FAILS (3 AttributeError on _entry_count_today_by_combo).**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_paper_broker_entry_count_today.py -v`

- [ ] **Step 3: Add the helper to `learning/paper_broker.py`.**

Read the file to find `_open_count_by_book` (Phase 2b helper, inside `class PaperBroker`). Add the new helper IMMEDIATELY AFTER it (same class, same indentation):

```python
    def _entry_count_today_by_combo(self, strategy: str, dte_bucket: str) -> int:
        """Count trades opened TODAY (in US/Eastern) for the given
        (strategy, dte_bucket) combo. Used by the Phase 3 intraday entry
        router to enforce INTRADAY_PER_COMBO_DAILY_CAP."""
        from datetime import date
        import pytz
        today_et = date.fromtimestamp(
            __import__("time").time()
        ).isoformat() if False else None   # placeholder, replaced below

        eastern = pytz.timezone("US/Eastern")
        from datetime import datetime as _dt
        today_et = _dt.now(eastern).date().isoformat()

        n = 0
        for t in self.trades.get_trades_by(strategy=strategy, dte_bucket=dte_bucket):
            # Trade dict has "entry_time" (e.g. "2026-05-26 10:15 AM EST")
            # or "date" — we look at whatever is the open-date field.
            entry_str = t.get("entry_time") or t.get("date") or ""
            if entry_str.startswith(today_et):
                n += 1
        return n
```

Wait, that has a placeholder. Let me give the cleaner version:

```python
    def _entry_count_today_by_combo(self, strategy: str, dte_bucket: str) -> int:
        """Count trades opened TODAY (in US/Eastern) for the given
        (strategy, dte_bucket) combo. Used by the Phase 3 intraday entry
        router to enforce INTRADAY_PER_COMBO_DAILY_CAP."""
        from datetime import datetime
        import pytz
        today_et = datetime.now(pytz.timezone("US/Eastern")).date().isoformat()

        n = 0
        for t in self.trades.get_trades_by(strategy=strategy, dte_bucket=dte_bucket):
            entry_str = t.get("entry_time") or t.get("date") or ""
            if entry_str.startswith(today_et):
                n += 1
        return n
```

Use the second version. The TradeRecorder records may use `entry_time` ("2026-05-26 10:15 AM EST") or just `date`; both start with the ISO date if formatted properly. The `startswith(today_et)` check handles either format.

- [ ] **Step 4: Run, verify all 3 PASS.**

- [ ] **Step 5: Run the FULL non-integration suite:**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: 753 + 3 = 756.

- [ ] **Step 6: Commit:**

```bash
git add learning/paper_broker.py tests/test_paper_broker_entry_count_today.py
git commit -m "feat: PaperBroker._entry_count_today_by_combo for Phase 3 dedup"
```

---

## Task 3: `signals/intraday_entry_router.py` — H2 + D rules

**Files:**
- Create: `signals/intraday_entry_router.py`
- Test: `tests/test_intraday_entry_router.py`

The router is a pure function: given a `SPYSetup` + current ET datetime + a `paper_broker`/`trade_recorder`, return a list of 0..2 setup_dicts ready for `paper_broker.execute_signal`. All H2 + D logic lives here.

- [ ] **Step 1: Write the failing tests — `tests/test_intraday_entry_router.py`:**

```python
"""Phase 3: intraday_entry_router applies H2 DTE assignment + D dedup
to convert a SPYSetup into 0..2 setup_dicts ready for execute_signal."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, date, time, timedelta
import pytz
import pytest

from signals.intraday_entry_router import route
from signals.spy_options_engine import SPYSetup
from learning.paper_broker import PaperBroker
from journal.trade_recorder import TradeRecorder

EASTERN = pytz.timezone("US/Eastern")


def _setup(strategy="call_debit_spread", conviction="high", score=75,
           direction="bullish"):
    return SPYSetup(
        strategy=strategy, conviction=conviction, timeframe="intraday",
        score=score, reasons=["test"], direction=direction, spy_price=500.0,
    )


def _now(hour=10, minute=0, weekday=2):
    """Build an ET datetime for a given hour/minute on a given weekday.
    Weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri."""
    # Find the next date with the requested weekday.
    d = date(2026, 5, 25 + weekday)   # 2026-05-25 is Monday
    return EASTERN.localize(datetime(d.year, d.month, d.day, hour, minute))


# ── H2 DTE assignment ────────────────────────────────────────────────────

def test_morning_high_conv_assigns_0dte(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=75)        # high-conv but not ultra
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "0DTE"
    assert result[0]["strategy"] == "call_debit_spread"
    assert result[0]["book"] == "disciplined"


def test_afternoon_high_conv_assigns_1_3dte(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=75)
    result = route(setup, _now(hour=14), broker)   # Wednesday 14:00 ET
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "1-3DTE"


def test_friday_pm_defaults_to_0dte(tmp_path, monkeypatch):
    """Friday safeguard: PM signal opens 0DTE (no weekend exposure)
    despite the time-of-day rule saying afternoon → 1-3DTE."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=75)
    result = route(setup, _now(hour=14, weekday=4), broker)   # Friday 14:00
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "0DTE"


def test_ultra_conv_morning_opens_both_dtes(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=92)        # ultra-conv (≥ 85)
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 2
    buckets = {r["dte_bucket"] for r in result}
    assert buckets == {"0DTE", "1-3DTE"}


def test_ultra_conv_on_friday_pm_only_0dte(tmp_path, monkeypatch):
    """Friday safeguard wins over ultra-conv doubling."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=92)        # ultra-conv
    result = route(setup, _now(hour=14, weekday=4), broker)   # Friday PM
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "0DTE"


# ── Entry-tier filter ────────────────────────────────────────────────────

def test_standard_conviction_returns_empty(tmp_path, monkeypatch):
    """Phase 3 ships with ENTRY_TIER_MINIMUM='high'. Standard-tier setups
    don't open positions."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=55, conviction="standard")
    assert route(setup, _now(hour=10), broker) == []


def test_watch_tier_returns_empty(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=30, conviction="watch")
    assert route(setup, _now(hour=10), broker) == []


# ── D dedup rule ─────────────────────────────────────────────────────────

def test_dedup_blocks_when_combo_already_open(tmp_path, monkeypatch):
    """If a (strategy, dte_bucket) position is already open, the router
    skips that bucket. With only-one-bucket H2 morning rule, this returns
    empty list."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1, trade_type="option_spread",
        strategy="call_debit_spread", direction="bullish", mode="intraday",
        legs=[], max_profit=200.0, max_loss=100.0,
        notes="[AUTO-PAPER] open", dte_bucket="0DTE", book="disciplined",
    )
    setup = _setup(strategy="call_debit_spread", score=78)
    result = route(setup, _now(hour=10), broker)
    assert result == []   # 0DTE already open + H2 morning rule = blocked


def test_dedup_blocks_when_per_day_cap_reached(tmp_path, monkeypatch):
    """After 2 closed trades for the combo today, further entries are blocked."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    # Seed 2 closed trades today for the combo (these count toward per-day cap).
    for _ in range(2):
        tid = rec.log_entry(
            ticker="SPY", entry_price=1.0, size=1, trade_type="option_spread",
            strategy="call_debit_spread", direction="bullish", mode="intraday",
            legs=[], max_profit=200.0, max_loss=100.0,
            notes="[AUTO-PAPER] test", dte_bucket="0DTE", book="disciplined",
        )
        rec.log_exit(tid, exit_price=0.50, exit_reason="stop")
    setup = _setup(strategy="call_debit_spread", score=78)
    result = route(setup, _now(hour=10), broker)
    assert result == []   # per-day cap = 2 already used


def test_ultra_conv_with_one_combo_blocked_returns_only_other(tmp_path, monkeypatch):
    """Ultra-conv morning normally returns both. If 0DTE is dedup-blocked
    but 1-3DTE isn't (despite the H2 morning rule saying just 0DTE), no —
    actually ultra-conv on morning = both buckets, and the H2 morning rule
    does NOT apply to ultra-conv. So if one bucket is blocked by dedup,
    return only the other."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    # Seed an open 0DTE position.
    rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1, trade_type="option_spread",
        strategy="iron_condor", direction="neutral", mode="intraday",
        legs=[], max_profit=200.0, max_loss=100.0,
        notes="[AUTO-PAPER] open", dte_bucket="0DTE", book="disciplined",
    )
    setup = _setup(strategy="iron_condor", conviction="high", score=92,
                    direction="neutral")
    result = route(setup, _now(hour=10), broker)   # ultra-conv morning
    # 0DTE blocked (open); 1-3DTE allowed → returns just 1-3DTE
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "1-3DTE"


# ── Setup_dict shape (placeholder pricing for Phase 3) ───────────────────

def test_setup_dict_has_expected_shape(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(strategy="iron_condor", score=78, direction="neutral")
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 1
    sd = result[0]
    # Required fields for execute_signal:
    assert "date" in sd
    assert sd["strategy"]   == "iron_condor"
    assert sd["dte_bucket"] == "0DTE"
    assert sd["book"]       == "disciplined"
    assert sd["direction"]  == "neutral"
    # Phase 3 placeholders (per spec — replaced by Phase 4 structure builder):
    assert sd["entry_price"] == 1.0
    assert sd["max_profit"]  == 200.0
    assert sd["max_loss"]    == 100.0
    assert sd["legs"]        == []
```

- [ ] **Step 2: Run, verify FAILS (ModuleNotFoundError: signals.intraday_entry_router).**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_entry_router.py -v`

- [ ] **Step 3: Create `signals/intraday_entry_router.py`:**

```python
"""
signals/intraday_entry_router.py -- Phase 3 entry-side decision module.

A pure function that takes a SPYSetup + current ET datetime + a PaperBroker
and returns 0..2 setup_dicts ready for PaperBroker.execute_signal.

Applies, in order:
  1. Entry-tier filter (conviction >= config.ENTRY_TIER_MINIMUM)
  2. H2 DTE assignment (time-of-day + Friday-PM safeguard + ultra-conv double)
  3. D dedup (one open per combo + per-day cap per combo)

Returns an empty list when all candidate buckets are blocked, or when the
setup fails the entry-tier filter. The MAX_CONCURRENT_DISCIPLINED cap is
enforced downstream inside execute_signal (Phase 2b), not here.

Phase 3 ships with placeholder pricing in the setup_dict (entry_price=1.0,
max_profit=200.0, max_loss=100.0, legs=[]). Phase 4's structure builder
will replace these with real per-sub-strategy strikes and pricing.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# Ordered tiers so we can compare ">=" against ENTRY_TIER_MINIMUM.
_TIER_RANK = {"watch": 0, "standard": 1, "high": 2}


def _passes_entry_tier(setup) -> bool:
    """Setup's conviction must rank >= config.ENTRY_TIER_MINIMUM."""
    return _TIER_RANK.get(setup.conviction, -1) >= _TIER_RANK.get(
        config.ENTRY_TIER_MINIMUM, _TIER_RANK["high"]
    )


def _assign_dte_buckets(setup, now: datetime) -> list[str]:
    """H2 rule: time-of-day discriminator with Friday-PM safeguard and
    ultra-conviction doubling.

    Returns the list of dte_buckets the setup should attempt to open in
    (before dedup filtering)."""
    # Parse the morning cutoff (HH:MM in ET) into a time object.
    cutoff_h, cutoff_m = (int(x) for x in config.INTRADAY_DTE_MORNING_CUTOFF.split(":"))
    morning_cutoff = time(cutoff_h, cutoff_m)

    is_friday    = now.weekday() == 4
    is_afternoon = now.time() >= morning_cutoff
    is_friday_pm = is_friday and is_afternoon

    # Ultra-conviction → both buckets, EXCEPT Friday PM (no weekend exposure).
    if setup.score >= config.ULTRA_CONVICTION_DOUBLE_DTE_SCORE and not is_friday_pm:
        return ["0DTE", "1-3DTE"]

    # Friday PM safeguard: always 0DTE, never 1-3DTE.
    if is_friday_pm:
        return ["0DTE"]

    # Default H2: morning → 0DTE, afternoon → 1-3DTE.
    return ["1-3DTE"] if is_afternoon else ["0DTE"]


def _dedup_filter(strategy: str, dte_buckets: list[str], broker) -> list[str]:
    """D rule: drop a bucket if a position is already open in (strategy, bucket)
    OR today's entry count for the combo has reached
    config.INTRADAY_PER_COMBO_DAILY_CAP."""
    allowed = []
    for bucket in dte_buckets:
        # Check 1: any position currently open in this combo?
        open_in_combo = [
            t for t in broker.trades.get_trades_by(strategy=strategy, dte_bucket=bucket)
            if t.get("outcome") == "open"
        ]
        if open_in_combo:
            continue

        # Check 2: today's entry count under the per-day cap?
        n_today = broker._entry_count_today_by_combo(strategy, bucket)
        if n_today >= config.INTRADAY_PER_COMBO_DAILY_CAP:
            continue

        allowed.append(bucket)
    return allowed


def _build_setup_dict(setup, dte_bucket: str, now: datetime) -> dict:
    """Construct a setup_dict in the shape PaperBroker.execute_signal expects.

    Phase 3 uses placeholder pricing values (entry_price=1.0, max_profit=200,
    max_loss=100, legs=[]). Phase 4 will replace these with real per-sub-
    strategy strikes from a structure builder."""
    return {
        "date":        now.date().isoformat(),
        "strategy":    setup.strategy,
        "dte_bucket":  dte_bucket,
        "book":        "disciplined",
        "direction":   (setup.direction or "neutral").lower(),
        # Phase 3 placeholders — see docstring + spec §Honesty Caveats.
        "entry_price": 1.0,
        "max_profit":  200.0,
        "max_loss":    100.0,
        "legs":        [],
    }


def route(setup, now: datetime, broker) -> list[dict]:
    """Convert a SPYSetup into 0..2 setup_dicts.

    setup:  a SPYSetup from signals.spy_options_engine
    now:    current datetime in US/Eastern (tz-aware)
    broker: a PaperBroker instance (needed for the dedup state queries)
    """
    if not _passes_entry_tier(setup):
        return []

    buckets    = _assign_dte_buckets(setup, now)
    allowed    = _dedup_filter(setup.strategy, buckets, broker)
    return [_build_setup_dict(setup, b, now) for b in allowed]
```

- [ ] **Step 4: Run focused tests, verify all 11 PASS.**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_entry_router.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run FULL non-integration suite to confirm no regressions:**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: 756 + 11 = 767.

- [ ] **Step 6: Confirm zero production consumers exist yet:**

Run: `grep -rn "from signals.intraday_entry_router\|import intraday_entry_router" scanners/ learning/ signals/ 2>/dev/null | grep -v tests`
Expected: empty output. (Wired in Task 4.)

- [ ] **Step 7: Commit:**

```bash
git add signals/intraday_entry_router.py tests/test_intraday_entry_router.py
git commit -m "feat: intraday_entry_router — H2 DTE assignment + D dedup (Phase 3)

Pure-function router converting a SPYSetup into 0..2 setup_dicts ready for
PaperBroker.execute_signal. Composes:
  - Entry-tier filter (conviction >= ENTRY_TIER_MINIMUM = 'high')
  - H2 DTE assignment (morning→0DTE, afternoon→1-3DTE, Fri-PM safeguard,
    ultra-conv ≥85 doubles to both buckets)
  - D dedup (one open per (strategy, dte_bucket) at a time + ≤2 entries/day
    per combo)

Phase 3 placeholder pricing baked into setup_dicts (entry_price=1.0,
max_profit=200, max_loss=100, legs=[]); Phase 4 replaces with real
per-sub-strategy structure builder. No production caller yet — Task 4
wires this into intraday_scanner."
```

---

## Task 4: Wire scanner → router → execute_signal

**Files:**
- Modify: `scanners/intraday_scanner.py` (inline wiring after each setup's alert posts)
- Test: `tests/test_intraday_scanner_pipeline.py`

The intraday_scanner already loops over setups and posts alerts. Phase 3 adds: after each alert, if the feature flag is on and `setup.conviction == "high"`, call the router; for each returned setup_dict, call `paper_broker.execute_signal`.

- [ ] **Step 1: Write the failing tests — `tests/test_intraday_scanner_pipeline.py`:**

```python
"""Phase 3: intraday_scanner wires high-conviction setups → execute_signal.

Flag default ON: scanner produces execute_signal calls for high-conv setups.
Flag OFF: scanner is byte-identical to Phase 2b (zero execute_signal calls).
Standard/watch conviction setups never trigger execute_signal regardless."""

import os, sys
from datetime import datetime, date
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest
import pytz

from scanners.intraday_scanner import IntradayScanner
from signals.spy_options_engine import SPYSetup

EASTERN = pytz.timezone("US/Eastern")


def _mk_setup(strategy="call_debit_spread", conviction="high", score=75,
              direction="bullish"):
    return SPYSetup(
        strategy=strategy, conviction=conviction, timeframe="intraday",
        score=score, reasons=["test"], direction=direction, spy_price=500.0,
    )


def _mk_market_hours_now():
    """A tz-aware ET datetime guaranteed to be inside market hours on a weekday."""
    return EASTERN.localize(datetime(2026, 5, 27, 10, 0))   # Wed 10:00 ET


def test_flag_off_produces_zero_execute_signal_calls(tmp_path, monkeypatch):
    """The kill-switch path: with INTRADAY_PAPER_BROKER_ENABLED = False,
    the scanner behaves byte-identically to Phase 2b — alerts fire but no
    paper position is opened."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", False)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(score=78)]
    scanner._fetch_alpaca = mock.Mock(return_value=pd.DataFrame({
        "close": [500.0] * 30, "high": [501.0] * 30, "low": [499.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=pd.date_range("2026-05-27 09:30", periods=30, freq="15min")))
    scanner.is_market_hours = mock.Mock(return_value=True)

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB:
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        # No execute_signal calls — flag was off.
        assert broker_inst.execute_signal.call_count == 0


def test_flag_on_high_conv_setup_triggers_execute_signal(tmp_path, monkeypatch):
    """Flag on + high-conviction setup → one execute_signal call (morning → 0DTE)."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", True)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(score=78)]
    scanner._fetch_alpaca = mock.Mock(return_value=pd.DataFrame({
        "close": [500.0] * 30, "high": [501.0] * 30, "low": [499.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=pd.date_range("2026-05-27 09:30", periods=30, freq="15min")))
    scanner.is_market_hours = mock.Mock(return_value=True)

    # Mock paper_broker.execute_signal so we observe the call without actually
    # writing to logs/trades.json.
    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB, \
         mock.patch("signals.intraday_entry_router.route") as router_mock:
        router_mock.return_value = [{
            "date": "2026-05-27", "strategy": "call_debit_spread",
            "dte_bucket": "0DTE", "book": "disciplined",
            "direction": "bullish", "entry_price": 1.0,
            "max_profit": 200.0, "max_loss": 100.0, "legs": [],
        }]
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        # Exactly one execute_signal call for the one router output.
        assert broker_inst.execute_signal.call_count == 1
        sd = broker_inst.execute_signal.call_args[0][0]
        assert sd["dte_bucket"] == "0DTE"
        assert sd["strategy"]   == "call_debit_spread"


def test_flag_on_standard_conv_does_not_trigger_execute_signal(tmp_path, monkeypatch):
    """Phase 3 ships with ENTRY_TIER_MINIMUM='high'. Standard-tier setups
    still alert, but never become positions."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", True)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(conviction="standard", score=55)]
    scanner._fetch_alpaca = mock.Mock(return_value=pd.DataFrame({
        "close": [500.0] * 30, "high": [501.0] * 30, "low": [499.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=pd.date_range("2026-05-27 09:30", periods=30, freq="15min")))
    scanner.is_market_hours = mock.Mock(return_value=True)

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB:
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        assert broker_inst.execute_signal.call_count == 0


def test_router_returning_two_dicts_produces_two_execute_signal_calls(tmp_path, monkeypatch):
    """Ultra-conviction path: router returns [0DTE_dict, 1-3DTE_dict] → two
    execute_signal calls."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", True)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(score=92)]   # ultra-conv
    scanner._fetch_alpaca = mock.Mock(return_value=pd.DataFrame({
        "close": [500.0] * 30, "high": [501.0] * 30, "low": [499.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=pd.date_range("2026-05-27 09:30", periods=30, freq="15min")))
    scanner.is_market_hours = mock.Mock(return_value=True)

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB, \
         mock.patch("signals.intraday_entry_router.route") as router_mock:
        router_mock.return_value = [
            {"date": "2026-05-27", "strategy": "call_debit_spread",
             "dte_bucket": "0DTE", "book": "disciplined", "direction": "bullish",
             "entry_price": 1.0, "max_profit": 200.0, "max_loss": 100.0, "legs": []},
            {"date": "2026-05-27", "strategy": "call_debit_spread",
             "dte_bucket": "1-3DTE", "book": "disciplined", "direction": "bullish",
             "entry_price": 1.0, "max_profit": 200.0, "max_loss": 100.0, "legs": []},
        ]
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        assert broker_inst.execute_signal.call_count == 2
        buckets = {c.args[0]["dte_bucket"] for c in broker_inst.execute_signal.call_args_list}
        assert buckets == {"0DTE", "1-3DTE"}
```

- [ ] **Step 2: Run, verify FAILS (4 failures — no execute_signal calls because scanner doesn't yet wire to broker).**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_scanner_pipeline.py -v`

- [ ] **Step 3: Read the current `scanners/intraday_scanner.py`.**

Find:
- The imports at the top.
- `class IntradayScanner` and its `__init__`.
- `_scan_spy_intraday()` method (around lines 130-200) — the loop that posts alerts.

The loop body currently ends with `alerts.append(alert)` after posting the alert. The Phase 3 addition slots in AFTER `alerts.append(alert)`.

- [ ] **Step 4: Add imports + wiring to `scanners/intraday_scanner.py`.**

Near the top, with the other `signals.*` imports, add:

```python
from signals.intraday_entry_router import route as _route_entry
from learning.paper_broker import PaperBroker
```

Inside `_scan_spy_intraday()`, find the existing `for setup in setups:` loop. After the existing `alerts.append(alert)` line (the final line of the loop body), add the Phase 3 wiring block:

```python
            # ── Phase 3: intraday entry pipeline ─────────────────────────
            # Convert high-conviction setups into paper positions via the
            # router + paper_broker.execute_signal. Gated by the feature flag.
            if not config.INTRADAY_PAPER_BROKER_ENABLED:
                continue
            if setup.conviction != "high":
                continue
            now_et = datetime.now(EASTERN)
            try:
                broker     = PaperBroker()
                setup_dicts = _route_entry(setup, now_et, broker)
                for sd in setup_dicts:
                    result = broker.execute_signal(sd)
                    logger.info(
                        f"Phase 3 entry: {sd['strategy']} @ {sd['dte_bucket']} → "
                        f"trade_id={result.get('trade_id')} recorded={result.get('recorded')}"
                    )
            except Exception as e:
                # Phase 3 wiring failure must NOT crash the scanner — it has
                # other tickers to handle and the alert side already posted.
                logger.exception(
                    f"Phase 3 entry pipeline error for {setup.strategy}: {e}"
                )
```

The `EASTERN` timezone constant is already imported at the top of the file (line ~20). `config` is already imported. `datetime` is already imported. `logger` is already in scope.

- [ ] **Step 5: Run focused tests, verify all 4 PASS.**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_scanner_pipeline.py -v`

- [ ] **Step 6: Run FULL non-integration suite to confirm no regressions:**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: 767 + 4 = 771. If any existing intraday-scanner test broke because it didn't expect the new code path, READ the test — the flag-off path should be byte-identical to today, so tests that don't set the flag should pass. Tests that explicitly enable the flag need to mock PaperBroker to avoid touching the trade journal.

- [ ] **Step 7: Commit:**

```bash
git add scanners/intraday_scanner.py tests/test_intraday_scanner_pipeline.py
git commit -m "feat: wire intraday_scanner → intraday_entry_router → execute_signal

Phase 3 final wiring. After each setup's alert posts, the scanner checks
config.INTRADAY_PAPER_BROKER_ENABLED (default True) and setup.conviction == 'high';
if both pass, calls intraday_entry_router.route() → for each returned
setup_dict, calls paper_broker.execute_signal().

Errors in the new path are caught and logged so the scanner's other work
(other tickers, future setups) continues. Flag-off path is byte-identical
to Phase 2b (verified by test).

Phase 3 ships with placeholder pricing in setup_dicts (entry_price=1.0,
max_profit=200, max_loss=100); Phase 4's structure builder replaces these
with real per-sub-strategy values. P&L on intraday trades is not yet
meaningful — Phase 3 tests the wiring + rules, not the dollars."
```

---

## Self-Review

**Spec coverage:**
- Single book (disciplined only) → Task 3 router hardcodes `book="disciplined"` in `_build_setup_dict` ✓
- Entry tier `high`, configurable via `ENTRY_TIER_MINIMUM` → Tasks 1 + 3 ✓
- Option D dedup (one open + per-day cap) → Task 3 `_dedup_filter` + Task 2 `_entry_count_today_by_combo` ✓
- H2 DTE assignment (time-of-day + Fri-PM safeguard + ultra-conv doubling) → Task 3 `_assign_dte_buckets` ✓
- MAX_CONCURRENT_DISCIPLINED enforcement → already in `paper_broker.execute_signal` from Phase 2b, no change needed ✓
- Feature flag `INTRADAY_PAPER_BROKER_ENABLED` default True → Tasks 1 + 4 ✓
- Inline wiring in `intraday_scanner` → Task 4 ✓
- Dedup state from TradeRecorder → Task 2 + Task 3 ✓
- Placeholder pricing → Task 3 docstring + `_build_setup_dict` literals ✓

**Placeholders:** none.

**Type consistency:**
- `route(setup, now, broker) -> list[dict]` — same signature in Task 3 module and Task 4 wiring.
- `_entry_count_today_by_combo(strategy, dte_bucket) -> int` — Task 2 defines; Task 3 calls.
- `_assign_dte_buckets`, `_dedup_filter`, `_build_setup_dict` — all internal to Task 3's router.
- `setup_dict` shape matches the existing `paper_broker.execute_signal` schema (Phase 2b).

**Dependency order:** Task 1 (config constants) → Task 2 (broker helper, uses constants) → Task 3 (router, uses helper + constants) → Task 4 (scanner wiring, uses router + flag). Strict linear chain.
