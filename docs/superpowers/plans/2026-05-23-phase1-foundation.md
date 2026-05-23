# Phase 1 — Foundation + Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the foundation that the multi-strategy / dual-book / 9-sub-strategy expansion will sit on, plus fix the one correctness bug in the existing learning loop (`hypothesis_runner` evaluating on in-sample-tuned data). None of these tasks change live trading behavior — they add capabilities, fix correctness, and extend data depth.

**Architecture:** Seven independent tasks, sequenced by dependency. Tasks 1-4 are pure additions (constants, dataclass extensions, model-pin update). Task 5 is the correctness fix (walk-forward + sample-size floor in the hypothesis-runner verdict). Task 6 is a one-line notification enhancement. Task 7 is a one-shot data-refresh script that extends `spy_history.csv` from ~5yr to ~32yr via free sources.

**Tech Stack:** Python, pandas, numpy. Adds `yfinance` to requirements.txt. Reuses `backtests/spy_daily_backtest.py`, `learning/knowledge_base.py`, `learning/hypothesis_runner.py`.

**Spec:** Derived from the strategic roadmap walkthrough (session 2026-05-23). No separate spec doc — the to-do list and phased plan in the session transcript are the design.

**Honest constraint:** This plan must ship before Tuesday 2026-05-26 market open. Phase 2-5 (intraday lifecycle, dual book, reflector refactor, meta-labeler revival) come in subsequent weeks.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `learning/reflector.py:35` | Modify | `CLAUDE_MODEL` pin → `claude-sonnet-4-6` |
| `learning/hypothesis_engine.py:46` | Modify | `CLAUDE_MODEL` pin → `claude-sonnet-4-6` |
| `learning/off_hours_learner.py:37` | Modify | `CLAUDE_MODEL` pin → `claude-sonnet-4-6` |
| `signals/morning_briefer.py:46` | Modify | `CLAUDE_MODEL` pin → `claude-sonnet-4-6` |
| `config.py` | Modify | Add INTRADAY EXIT-RULE CONSTANTS block (9 sub-strategies × ~3 rules each) |
| `learning/hypothesis_engine.py:50` | Modify | Extend `TUNABLE_PARAMS` with new constants + bounded `STOP_PCT_45DTE` |
| `learning/knowledge_base.py:54-68` | Modify | Add `strategy` / `dte_bucket` / `book` optional fields to `KBEntry` |
| `learning/knowledge_base.py:76+` | Modify | Add `KnowledgeBase.search()` filter API |
| `learning/hypothesis_runner.py:160-210` | Modify | Walk-forward + OOS verdict + ≥30 OOS-trade floor |
| `learning/hypothesis_runner.py:132-146` | Modify | Notification includes pending-promotion count + ≥5 alert |
| `backtests/refresh_all_history.py` (new) | Create | One-shot data refresh: SPY 1993+ (yfinance), VIX family (CBOE), yield curve (FRED), sector ETFs |
| `requirements.txt` | Modify | Add `yfinance` |
| Tests under `tests/` | Various | Per task |

---

## Task 1: Sonnet 4.6 model pin

**Files:**
- Modify: `learning/reflector.py`, `learning/hypothesis_engine.py`, `learning/off_hours_learner.py`, `signals/morning_briefer.py` (four files, one line each)
- Test: `tests/test_model_pin.py`

The codebase has four module-level `CLAUDE_MODEL = "claude-sonnet-4-5-20250929"` constants. They should all be on the current stable. `signals/context_analyst.py` already uses `claude-sonnet-4-6` as `ESCALATE_MODEL`; use the same exact string.

- [ ] **Step 1: Write the failing test — `tests/test_model_pin.py`:**

```python
"""Smoke test: every place that pins a Sonnet model name uses the current stable."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import learning.reflector            as reflector
import learning.hypothesis_engine    as hyp_engine
import learning.off_hours_learner    as off_hours
import signals.morning_briefer       as briefer

CURRENT_SONNET = "claude-sonnet-4-6"


def test_all_sonnet_pins_are_current():
    assert reflector.CLAUDE_MODEL.startswith(CURRENT_SONNET)
    assert hyp_engine.CLAUDE_MODEL.startswith(CURRENT_SONNET)
    assert off_hours.CLAUDE_MODEL.startswith(CURRENT_SONNET)
    assert briefer.CLAUDE_MODEL.startswith(CURRENT_SONNET)
```

- [ ] **Step 2: Run the test, verify it FAILS.**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_model_pin.py -v`
Expected: 1 failure — the pins are still on `claude-sonnet-4-5-20250929`.

- [ ] **Step 3: Update the four pins.**

In each of these four files, replace the line:

```python
CLAUDE_MODEL   = "claude-sonnet-4-5-20250929"
```

with:

```python
CLAUDE_MODEL   = "claude-sonnet-4-6"
```

The four files (exact paths):
- `learning/reflector.py` (line 35)
- `learning/hypothesis_engine.py` (line 46)
- `learning/off_hours_learner.py` (line 37)
- `signals/morning_briefer.py` (line 46)

- [ ] **Step 4: Run the test, verify it PASSES.**

Expected: 1 passed.

- [ ] **Step 5: Commit.**

```bash
git add learning/reflector.py learning/hypothesis_engine.py learning/off_hours_learner.py signals/morning_briefer.py tests/test_model_pin.py
git commit -m "chore: bump Sonnet pin to 4.6 across learning + briefer modules"
```

---

## Task 2: Per-sub-strategy exit-rule constants in `config.py`

**Files:**
- Modify: `config.py` (add a new section AFTER the existing `INTRADAY-TOUCH EXIT` block; module-level)
- Test: `tests/test_intraday_exit_rule_constants.py`

These are pure data — nothing reads them in Phase 1. Phase 2's strategy-aware `ExitManager` refactor will. Adding them now means the hypothesis engine (Task 3) can put them in its tunable whitelist.

- [ ] **Step 1: Write the failing test — `tests/test_intraday_exit_rule_constants.py`:**

```python
"""Phase 1: per-sub-strategy exit-rule constants are declared in config.py.

Nothing CONSUMES them in Phase 1 — they're foundation. Phase 2's strategy-aware
ExitManager refactor will read them. We test their presence + sane defaults.
"""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_45dte_constants_match_current_defaults():
    # 45DTE: keep today's values (no live behavior change in Phase 1).
    assert config.PROFIT_TARGET_PCT_45DTE_CALL   == 0.70
    assert config.PROFIT_TARGET_PCT_45DTE_PUT    == 0.70
    assert config.PROFIT_TARGET_PCT_45DTE_COND   == 0.70
    assert config.DTE_CLOSE_THRESHOLD_45DTE      == 21
    # Experimental 45DTE stop (default None = no stop, matches current behavior).
    assert config.STOP_PCT_45DTE                  is None


def test_1_3dte_constants_are_aggressive():
    assert config.PROFIT_TARGET_PCT_1_3DTE_CALL  == 0.50
    assert config.PROFIT_TARGET_PCT_1_3DTE_PUT   == 0.50
    assert config.PROFIT_TARGET_PCT_1_3DTE_COND  == 0.50
    assert config.STOP_PCT_1_3DTE_CALL           == 0.50
    assert config.STOP_PCT_1_3DTE_PUT            == 0.50
    # condor exits on short-strike touch + force-close before bell
    assert config.FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE == 30
    assert config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_1_3DTE     is True


def test_0dte_constants_are_most_aggressive():
    # 0DTE: target 100% (credit doubled) for debits, 30% for condors (faster).
    assert config.PROFIT_TARGET_PCT_0DTE_CALL    == 1.00
    assert config.PROFIT_TARGET_PCT_0DTE_PUT     == 1.00
    assert config.PROFIT_TARGET_PCT_0DTE_COND    == 0.30
    assert config.STOP_PCT_0DTE_CALL             == 0.75
    assert config.STOP_PCT_0DTE_PUT              == 0.75
    # Force-close times of day (gamma risk into the bell). HH:MM strings, ET.
    assert config.FORCED_CLOSE_TIME_0DTE_DEBIT   == "15:30"
    assert config.FORCED_CLOSE_TIME_0DTE_CONDOR  == "15:00"
    assert config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_0DTE      is True
```

- [ ] **Step 2: Run, verify FAILS** (`AttributeError: module 'config' has no attribute 'PROFIT_TARGET_PCT_45DTE_CALL'`).

- [ ] **Step 3: Add the constants block to `config.py`.**

Find the existing block that starts with `# INTRADAY-TOUCH EXIT (backtest ship-bar floors)`. Immediately AFTER its last line (`INTRADAY_TOUCH_SHIP_MIN_ATTRIB = 0.15`), add this:

```python


# ─────────────────────────────────────────
# PER-SUB-STRATEGY EXIT RULES
# ─────────────────────────────────────────
# Foundation for the multi-strategy expansion (Phase 2 will wire these into a
# strategy-aware ExitManager). Three strategies × three DTE buckets = 9
# sub-strategies; each gets its own exit-rule tuple. Naming convention:
#   PROFIT_TARGET_PCT_{DTE_BUCKET}_{STRUCTURE}
#   STOP_PCT_{DTE_BUCKET}_{STRUCTURE}
#   FORCED_CLOSE_TIME_{DTE_BUCKET}_{STRUCTURE}  (HH:MM ET, for 0DTE)
#   FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_{DTE_BUCKET}  (for 1-3DTE)
# Where STRUCTURE in {CALL (call_debit_spread), PUT (put_debit_spread), COND
# (iron_condor)}.

# 45 DTE — keep today's tuned values (no live behavior change in Phase 1).
PROFIT_TARGET_PCT_45DTE_CALL    = 0.70
PROFIT_TARGET_PCT_45DTE_PUT     = 0.70
PROFIT_TARGET_PCT_45DTE_COND    = 0.70
DTE_CLOSE_THRESHOLD_45DTE       = 21
# Experimental: None = no stop (current behavior). Hypothesis engine may
# propose a bounded value via TUNABLE_PARAMS to test if a hard stop helps.
STOP_PCT_45DTE                  = None

# 1-3 DTE — theta is faster, gamma is closer; smaller targets, real stops.
PROFIT_TARGET_PCT_1_3DTE_CALL   = 0.50
PROFIT_TARGET_PCT_1_3DTE_PUT    = 0.50
PROFIT_TARGET_PCT_1_3DTE_COND   = 0.50
STOP_PCT_1_3DTE_CALL            = 0.50
STOP_PCT_1_3DTE_PUT             = 0.50
CONDOR_SHORT_STRIKE_TOUCH_EXIT_1_3DTE       = True
FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE   = 30

# 0 DTE — gamma is everything; never let it expire.
PROFIT_TARGET_PCT_0DTE_CALL     = 1.00       # 100% (credit doubled) for debits
PROFIT_TARGET_PCT_0DTE_PUT      = 1.00
PROFIT_TARGET_PCT_0DTE_COND     = 0.30       # smaller + faster for condors
STOP_PCT_0DTE_CALL              = 0.75
STOP_PCT_0DTE_PUT               = 0.75
CONDOR_SHORT_STRIKE_TOUCH_EXIT_0DTE = True
FORCED_CLOSE_TIME_0DTE_DEBIT    = "15:30"    # ET, HH:MM
FORCED_CLOSE_TIME_0DTE_CONDOR   = "15:00"    # ET — gamma into the bell
```

- [ ] **Step 4: Run, verify PASSES** (3 tests passed).

- [ ] **Step 5: Commit.**

```bash
git add config.py tests/test_intraday_exit_rule_constants.py
git commit -m "feat: per-sub-strategy exit-rule constants in config (Phase 2 prep)"
```

---

## Task 3: Extend `TUNABLE_PARAMS` with new constants + bounded `STOP_PCT_45DTE`

**Files:**
- Modify: `learning/hypothesis_engine.py` (the `TUNABLE_PARAMS` dict, around line 50)
- Test: `tests/test_tunable_params.py`

Add the new exit-rule constants from Task 2 to the whitelist so the hypothesis engine can propose changes to them. `STOP_PCT_45DTE` is special — its default is `None` (no stop); the engine needs both an enable-bit and a value-range.

- [ ] **Step 1: Write the failing test — `tests/test_tunable_params.py`:**

```python
"""Phase 1: TUNABLE_PARAMS includes per-sub-strategy exit-rule constants."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from learning.hypothesis_engine import TUNABLE_PARAMS


def test_existing_params_still_whitelisted():
    # Sanity: don't accidentally drop entries during the refactor.
    assert ("signals.regime_detector", "ADX_TREND_MIN")          in TUNABLE_PARAMS
    assert ("signals.regime_detector", "EXTENDED_TREND_MAX_PCT") in TUNABLE_PARAMS
    assert ("learning.exit_manager",   "PROFIT_TARGET_PCT")      in TUNABLE_PARAMS


def test_45dte_stop_added_with_bounds_and_nullable():
    rule = TUNABLE_PARAMS[("config", "STOP_PCT_45DTE")]
    assert rule["type"]     == "float_or_none"   # special: None = disable
    assert rule["min"]      == 0.60
    assert rule["max"]      == 0.90
    # The engine may propose either None (no change from default) or a float
    # in [min, max]. The type marker is how the engine + runner know to allow None.


def test_0dte_exit_rules_whitelisted():
    for var in ("PROFIT_TARGET_PCT_0DTE_CALL", "PROFIT_TARGET_PCT_0DTE_PUT",
                "PROFIT_TARGET_PCT_0DTE_COND",
                "STOP_PCT_0DTE_CALL", "STOP_PCT_0DTE_PUT"):
        assert ("config", var) in TUNABLE_PARAMS, var
    targets = TUNABLE_PARAMS[("config", "PROFIT_TARGET_PCT_0DTE_CALL")]
    assert targets["type"] == "float"
    assert targets["min"] >= 0.20 and targets["max"] <= 2.00


def test_1_3dte_exit_rules_whitelisted():
    for var in ("PROFIT_TARGET_PCT_1_3DTE_CALL", "PROFIT_TARGET_PCT_1_3DTE_PUT",
                "PROFIT_TARGET_PCT_1_3DTE_COND",
                "STOP_PCT_1_3DTE_CALL", "STOP_PCT_1_3DTE_PUT"):
        assert ("config", var) in TUNABLE_PARAMS, var


def test_45dte_profit_targets_whitelisted():
    # The 45DTE profit targets should also be tunable now (we split the single
    # PROFIT_TARGET_PCT into per-structure constants in Task 2; old global stays
    # for back-compat but the new per-structure constants are what we'll tune).
    for var in ("PROFIT_TARGET_PCT_45DTE_CALL", "PROFIT_TARGET_PCT_45DTE_PUT",
                "PROFIT_TARGET_PCT_45DTE_COND"):
        assert ("config", var) in TUNABLE_PARAMS, var
```

- [ ] **Step 2: Run, verify FAILS** (5 tests fail — new entries missing).

- [ ] **Step 3: Extend `TUNABLE_PARAMS` in `learning/hypothesis_engine.py`.**

Replace the existing `TUNABLE_PARAMS = { ... }` block (around line 50) with:

```python
TUNABLE_PARAMS = {
    # ── Existing regime + per-day knobs (keep) ──────────────────────────────
    ("signals.regime_detector", "ADX_TREND_MIN"):            {"type": "float", "min": 15.0, "max": 35.0},
    ("signals.regime_detector", "VIX_CALM_MAX"):             {"type": "float", "min": 12.0, "max": 22.0},
    ("signals.regime_detector", "MIN_TREND_SEPARATION_PCT"): {"type": "float", "min": 0.5,  "max": 3.0},
    ("signals.regime_detector", "EXTENDED_TREND_MAX_PCT"):   {"type": "float", "min": 5.0,  "max": 15.0},
    ("signals.options_layer",   "MIN_CREDIT_SPREAD_RR"):     {"type": "float", "min": 0.20, "max": 0.75},
    ("learning.exit_manager",   "PROFIT_TARGET_PCT"):        {"type": "float", "min": 0.40, "max": 0.90},
    ("learning.exit_manager",   "DTE_CLOSE_THRESHOLD"):      {"type": "int",   "min": 7,    "max": 30},
    ("config",                  "SCORE_ALERT_MINIMUM"):      {"type": "int",   "min": 30,   "max": 75},
    ("config",                  "SCORE_HIGH_CONVICTION"):    {"type": "int",   "min": 55,   "max": 90},
    ("config",                  "MIN_RISK_REWARD_RATIO"):    {"type": "float", "min": 1.0,  "max": 3.0},
    ("config",                  "IC_RANGE_THRESHOLD_PCT"):   {"type": "float", "min": 1.5,  "max": 4.0},

    # ── Phase 1 additions: per-sub-strategy exit rules ──────────────────────
    # 45DTE per-structure profit targets (split from the global PROFIT_TARGET_PCT)
    ("config", "PROFIT_TARGET_PCT_45DTE_CALL"):  {"type": "float", "min": 0.40, "max": 0.90},
    ("config", "PROFIT_TARGET_PCT_45DTE_PUT"):   {"type": "float", "min": 0.40, "max": 0.90},
    ("config", "PROFIT_TARGET_PCT_45DTE_COND"):  {"type": "float", "min": 0.40, "max": 0.90},

    # Experimental 45DTE stop. Type "float_or_none" means the engine may propose
    # either None (disable, default) or a float in [min, max] (enable at that level).
    ("config", "STOP_PCT_45DTE"):                {"type": "float_or_none", "min": 0.60, "max": 0.90},

    # 1-3DTE
    ("config", "PROFIT_TARGET_PCT_1_3DTE_CALL"): {"type": "float", "min": 0.30, "max": 0.80},
    ("config", "PROFIT_TARGET_PCT_1_3DTE_PUT"):  {"type": "float", "min": 0.30, "max": 0.80},
    ("config", "PROFIT_TARGET_PCT_1_3DTE_COND"): {"type": "float", "min": 0.30, "max": 0.80},
    ("config", "STOP_PCT_1_3DTE_CALL"):          {"type": "float", "min": 0.40, "max": 0.80},
    ("config", "STOP_PCT_1_3DTE_PUT"):           {"type": "float", "min": 0.40, "max": 0.80},

    # 0DTE
    ("config", "PROFIT_TARGET_PCT_0DTE_CALL"):   {"type": "float", "min": 0.20, "max": 2.00},
    ("config", "PROFIT_TARGET_PCT_0DTE_PUT"):    {"type": "float", "min": 0.20, "max": 2.00},
    ("config", "PROFIT_TARGET_PCT_0DTE_COND"):   {"type": "float", "min": 0.15, "max": 0.60},
    ("config", "STOP_PCT_0DTE_CALL"):            {"type": "float", "min": 0.50, "max": 0.90},
    ("config", "STOP_PCT_0DTE_PUT"):             {"type": "float", "min": 0.50, "max": 0.90},
}
```

- [ ] **Step 4: Run, verify all 5 PASS.**

- [ ] **Step 5: Commit.**

```bash
git add learning/hypothesis_engine.py tests/test_tunable_params.py
git commit -m "feat: extend TUNABLE_PARAMS with per-sub-strategy exit rules + bounded STOP_PCT_45DTE"
```

---

## Task 4: `KBEntry` tags + `KnowledgeBase.search()` filter API

**Files:**
- Modify: `learning/knowledge_base.py` (extend `KBEntry` dataclass + add `search()` method)
- Test: `tests/test_kb_tags_search.py`

Adds three optional tag fields (`strategy`, `dte_bucket`, `book`) to `KBEntry`. All default to `None` so existing entries deserialize unchanged. Adds a `KnowledgeBase.search()` method that filters by these tags. Old entries without tags are excluded from filtered searches — that's the intended semantic.

- [ ] **Step 1: Write the failing test — `tests/test_kb_tags_search.py`:**

```python
"""Phase 1: KBEntry has strategy/dte_bucket/book optional tags; KnowledgeBase
exposes search() that filters by them."""

import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from learning.knowledge_base import KBEntry, KnowledgeBase


def test_kbentry_new_fields_default_to_none():
    e = KBEntry(date="2026-05-23", category="regime_accuracy", claim="x")
    assert e.strategy   is None
    assert e.dte_bucket is None
    assert e.book       is None


def test_kbentry_new_fields_accept_strings():
    e = KBEntry(date="2026-05-23", category="exit_timing", claim="x",
                strategy="iron_condor", dte_bucket="0DTE", book="learning")
    assert e.strategy   == "iron_condor"
    assert e.dte_bucket == "0DTE"
    assert e.book       == "learning"


def test_kbentry_roundtrip_through_jsonl(tmp_path, monkeypatch):
    """Existing entries lack the new fields; they must still round-trip."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    kb = KnowledgeBase()
    # Tagged
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="tagged",
                      strategy="iron_condor", dte_bucket="0DTE", book="disciplined"))
    # Untagged (old shape)
    kb.append(KBEntry(date="2026-05-23", category="regime_accuracy", claim="untagged"))
    rows = kb.all()
    assert len(rows) == 2
    tagged   = next(r for r in rows if r["claim"] == "tagged")
    untagged = next(r for r in rows if r["claim"] == "untagged")
    assert tagged["strategy"]      == "iron_condor"
    assert untagged.get("strategy") is None


def test_search_filters_by_strategy(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    kb = KnowledgeBase()
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="a",
                      strategy="iron_condor", dte_bucket="0DTE", book="disciplined"))
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="b",
                      strategy="bull_debit", dte_bucket="1-3DTE", book="disciplined"))
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="c"))  # untagged

    only_condor = kb.search(strategy="iron_condor")
    assert [r["claim"] for r in only_condor] == ["a"]

    only_disciplined = kb.search(book="disciplined")
    assert sorted([r["claim"] for r in only_disciplined]) == ["a", "b"]

    only_0dte_condor_disciplined = kb.search(strategy="iron_condor",
                                              dte_bucket="0DTE",
                                              book="disciplined")
    assert [r["claim"] for r in only_0dte_condor_disciplined] == ["a"]


def test_search_no_filter_returns_all(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    kb = KnowledgeBase()
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="x",
                      strategy="iron_condor"))
    kb.append(KBEntry(date="2026-05-23", category="exit_timing", claim="y"))
    assert len(kb.search()) == 2   # no filter = all
```

- [ ] **Step 2: Run, verify FAILS** (no `strategy` field on `KBEntry`; no `search()` method).

- [ ] **Step 3: Extend `KBEntry` dataclass in `learning/knowledge_base.py`.**

Find the `KBEntry` dataclass (around line 54-68). Replace the `id` field's line with the following block (adds three new optional fields BEFORE `id` to keep the `id` field as the last one — that's the constructor-default convention):

```python
    date:       str
    category:   str
    claim:      str
    evidence:   str            = ""
    confidence: float          = 0.5
    source:     str            = "reflector"   # reflector / off_hours / hypothesis / manual
    tags:       list[str]      = field(default_factory=list)
    strategy:   str | None     = None   # e.g. "iron_condor", "bull_debit", "put_debit_spread"
    dte_bucket: str | None     = None   # "0DTE" / "1-3DTE" / "45DTE"
    book:       str | None     = None   # "disciplined" / "learning"
    id:         str            = field(default_factory=lambda: uuid.uuid4().hex[:10])
```

- [ ] **Step 4: Add `KnowledgeBase.search()` method.**

Inside the `KnowledgeBase` class in `learning/knowledge_base.py`, add this method (place it AFTER `by_category` and BEFORE `stats`):

```python
    def search(self, *, strategy: str | None = None,
               dte_bucket: str | None = None,
               book: str | None = None,
               category: str | None = None,
               days: int | None = None) -> list[dict]:
        """Filter KB entries by optional tag values. Entries that lack a tag
        are EXCLUDED from filters that specify that tag — old (untagged)
        entries don't participate in strategy/book/dte_bucket searches.

        No-filter call returns all entries.
        """
        rows = self.recent(days=days) if days is not None else self.all()
        if strategy is not None:
            rows = [r for r in rows if r.get("strategy") == strategy]
        if dte_bucket is not None:
            rows = [r for r in rows if r.get("dte_bucket") == dte_bucket]
        if book is not None:
            rows = [r for r in rows if r.get("book") == book]
        if category is not None:
            rows = [r for r in rows if r.get("category") == category]
        return rows
```

- [ ] **Step 5: Run, verify all 5 PASS.**

- [ ] **Step 6: Quick smoke that we haven't broken serialization for existing reflector use.**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -c "
from learning.knowledge_base import KBEntry, KnowledgeBase
import json
e = KBEntry(date='2026-05-23', category='regime_accuracy', claim='smoke', evidence='x', confidence=0.7)
# asdict equivalent — verify the new fields default cleanly
from dataclasses import asdict
d = asdict(e)
assert d['strategy'] is None and d['dte_bucket'] is None and d['book'] is None
print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit.**

```bash
git add learning/knowledge_base.py tests/test_kb_tags_search.py
git commit -m "feat: KBEntry strategy/dte_bucket/book tags + KnowledgeBase.search() filter"
```

---

## Task 5: `hypothesis_runner` walk-forward + OOS verdict + sample-size floor

**This is the correctness fix.** Currently `_default_backtest` runs the modified backtest over the WHOLE 5-year period and the verdict compares full-history sharpe/pnl deltas. That's in-sample-on-tuning-data — the original `ADX_TREND_MIN=32` etc. were themselves tuned on this same period. Going forward, the verdict must be **OOS only** (last 40% of dates, the same 60/40 split we used in every walk-forward this session), and must require at least 30 OOS trades to be conclusive.

**Files:**
- Modify: `learning/hypothesis_runner.py` (rewrite `_default_backtest` to return IS/OOS split; rewrite `_verdict` to use OOS metrics; update `run()` to compute OOS deltas; update KB-entry evidence)
- Test: `tests/test_hypothesis_runner_oos.py`

### Step 1: Write the failing test — `tests/test_hypothesis_runner_oos.py`

```python
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
```

### Step 2: Run, verify FAILS

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_hypothesis_runner_oos.py -v`
Expected: 6 failures — `MIN_OOS_TRADES` doesn't exist, `_deltas` is not a classmethod, `_verdict` doesn't take a `modified` arg.

### Step 3: Refactor `_default_backtest` to return IS / OOS split

In `learning/hypothesis_runner.py`, REPLACE the entire `@staticmethod def _default_backtest(override: tuple | None) -> dict:` method (around lines 168-211) with:

```python
    @staticmethod
    def _default_backtest(override: tuple | None) -> dict:
        """
        Run the SPY daily backtest, optionally with a module-var override.
        Returns full-history stats PLUS in-sample (first 60% of dates) and
        out-of-sample (last 40% of dates) blocks. The verdict reads OOS.

        Override is restored in a finally block so a failed run can't leak
        a patched value into other modules.
        """
        import numpy as np
        import pandas as pd
        from backtests.spy_daily_backtest import SPYBacktest, BacktestDataLoader
        from data.event_calendar import EventCalendar

        original = None
        target_module = None
        if override is not None:
            module_path, var_name, new_value = override
            target_module = importlib.import_module(module_path)
            original = getattr(target_module, var_name)
            setattr(target_module, var_name, new_value)

        try:
            loader = BacktestDataLoader()
            spy_df, vix_df = loader.load(years=BACKTEST_YEARS, source="local")
            cal = EventCalendar()
            df  = SPYBacktest(spy_df, vix_df, cal, years=BACKTEST_YEARS).run()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            # 60/40 chronological split — matches every other walk-forward we've used.
            cut       = int(len(df) * 0.60)
            is_slice  = df.iloc[:cut]
            oos_slice = df.iloc[cut:]

            full = HypothesisRunner._metrics_block(df)
            full["is"]  = HypothesisRunner._metrics_block(is_slice)
            full["oos"] = HypothesisRunner._metrics_block(oos_slice)
            return full
        finally:
            if override is not None and target_module is not None:
                setattr(target_module, override[1], original)

    @staticmethod
    def _metrics_block(df) -> dict:
        """Compute {trades, win_rate, pnl, sharpe} for a slice of backtest rows."""
        import numpy as np
        traded = df[df["tradeable"] == True]
        closed = traded[traded["outcome"].isin(["win", "loss", "breakeven"])]
        n      = len(closed)
        wins   = len(closed[closed["outcome"] == "win"])
        wr     = round(wins / n * 100, 1) if n else 0.0
        pnl    = int(traded["pnl"].sum()) if len(traded) else 0
        daily  = traded["pnl"].values
        sharpe = float((np.mean(daily) / (np.std(daily) + 1e-9)) * np.sqrt(252)) if len(daily) > 1 else 0.0
        return {
            "trades":   int(n),
            "win_rate": wr,
            "pnl":      pnl,
            "sharpe":   round(sharpe, 3),
        }
```

### Step 4: Replace `_verdict` + add `_deltas`

In the same file, REPLACE the existing `@staticmethod def _verdict(deltas: dict) -> str:` method (around line 153) with this pair:

```python
    @staticmethod
    def _deltas(baseline: dict, modified: dict) -> dict:
        """Compute OOS deltas (used by verdict), IS deltas (context for KB),
        and aggregate deltas (back-compat with existing KB-entry readers)."""
        return {
            # OOS deltas — what the verdict gates on.
            "oos_trades_delta":   modified["oos"]["trades"]   - baseline["oos"]["trades"],
            "oos_win_rate_delta": round(modified["oos"]["win_rate"] - baseline["oos"]["win_rate"], 2),
            "oos_pnl_delta":      modified["oos"]["pnl"]      - baseline["oos"]["pnl"],
            "oos_sharpe_delta":   round(modified["oos"]["sharpe"] - baseline["oos"]["sharpe"], 3),
            # IS deltas — context only.
            "is_pnl_delta":       modified["is"]["pnl"]      - baseline["is"]["pnl"],
            "is_sharpe_delta":    round(modified["is"]["sharpe"] - baseline["is"]["sharpe"], 3),
            # Aggregate deltas — back-compat with existing KB-entry consumers.
            "trades_delta":       modified["trades"]   - baseline["trades"],
            "win_rate_delta":     round(modified["win_rate"] - baseline["win_rate"], 2),
            "pnl_delta":          modified["pnl"]      - baseline["pnl"],
            "sharpe_delta":       round(modified["sharpe"] - baseline["sharpe"], 3),
        }

    @staticmethod
    def _verdict(deltas: dict, modified: dict) -> str:
        """OOS-based verdict with sample-size floor.

        Auto-inconclusive if the affected OOS slice has < MIN_OOS_TRADES (30)
        — small samples can't honestly support either acceptance or rejection.
        """
        if modified["oos"]["trades"] < MIN_OOS_TRADES:
            return "inconclusive"
        oos_sd = deltas["oos_sharpe_delta"]
        oos_pd = deltas["oos_pnl_delta"]
        if oos_sd >= SHARPE_ACCEPT_DELTA and oos_pd > 0:
            return "accepted"
        if oos_sd <= SHARPE_REJECT_DELTA or oos_pd <= PNL_REJECT_DELTA:
            return "rejected"
        return "inconclusive"
```

### Step 5: Add the `MIN_OOS_TRADES` constant + update imports

Near the top of `learning/hypothesis_runner.py`, find the line `PNL_REJECT_DELTA = -250` (around line 38). Immediately after it, add:

```python
MIN_OOS_TRADES        = 30    # below this floor in the OOS slice → auto-inconclusive
```

### Step 6: Update `run()` to use the new shape

In the `run()` method (around line 85), find this block:

```python
        deltas = {
            "trades_delta":   modified["trades"]   - baseline["trades"],
            "win_rate_delta": round(modified["win_rate"] - baseline["win_rate"], 2),
            "pnl_delta":      modified["pnl"]      - baseline["pnl"],
            "sharpe_delta":   round(modified["sharpe"] - baseline["sharpe"], 3),
        }
        verdict = self._verdict(deltas)
```

Replace it with:

```python
        deltas = self._deltas(baseline, modified)
        verdict = self._verdict(deltas, modified)
```

### Step 7: Update the KB-entry evidence string

In the same `run()` method, find the `self.kb.append(KBEntry(...))` block and replace the `evidence=` argument with:

```python
            evidence   = (
                f"OOS Δsharpe {deltas['oos_sharpe_delta']:+.3f}, "
                f"OOS Δpnl {deltas['oos_pnl_delta']:+}, "
                f"OOS trades {modified['oos']['trades']}; "
                f"IS Δsharpe {deltas['is_sharpe_delta']:+.3f}, "
                f"IS Δpnl {deltas['is_pnl_delta']:+}"
            ),
```

### Step 8: Update the accept notification to show OOS deltas

In the same `run()` method, find the `if verdict == "accepted" and self._post_fn:` block. Replace the `self._post_fn(...)` argument with:

```python
                self._post_fn(
                    f"**Hypothesis accepted: {spec.get('id')}**\n"
                    f"{spec.get('module')}.{spec.get('var')}: "
                    f"{spec.get('current_value')} → {spec.get('proposed_value')}\n"
                    f"OOS ΔSharpe {deltas['oos_sharpe_delta']:+.2f} · "
                    f"OOS ΔP&L {deltas['oos_pnl_delta']:+,} "
                    f"(n={modified['oos']['trades']} OOS trades)\n\n"
                    f"Apply with: python -m learning.promote {spec.get('id')}"
                )
```

### Step 9: Run, verify all 6 tests PASS

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_hypothesis_runner_oos.py -v`
Expected: 6 passed.

### Step 10: Run the existing hypothesis_runner test (if any) + full suite for parity

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q`
Expected: all pass. If a pre-existing test broke because it asserted on the old verdict signature or delta keys, that's a real signal — read the test, update it to use the new `_verdict(deltas, modified)` signature and `oos_*` delta keys. **Do not delete pre-existing tests.**

### Step 11: Commit

```bash
git add learning/hypothesis_runner.py tests/test_hypothesis_runner_oos.py
git commit -m "fix(correctness): hypothesis_runner verdict on OOS only + 30-trade floor

Every accepted hypothesis to date was evaluated by comparing full-history
sharpe/pnl deltas where the baseline thresholds were themselves tuned on
the same period — pure in-sample optimization. This refactor splits each
backtest into IS (first 60% of dates) + OOS (last 40%), gates the verdict
on OOS metrics only, and auto-returns 'inconclusive' when the OOS slice
has < 30 trades.

The runner now writes both OOS and IS deltas into KB entries + accept
notifications so the texture of the result is visible.

Back-compat preserved: aggregate sharpe_delta / pnl_delta keys still
appear in the deltas dict for any external reader."
```

---

## Task 6: Pending-promotion count in hypothesis-runner notification

**Files:**
- Modify: `learning/hypothesis_runner.py` (in `run()`, the accept-notification block from Task 5)
- Test: `tests/test_pending_promotion_count.py`

Add a helper that counts files in `logs/learning/hypotheses/` with `status == "accepted"`. Include the count in the Discord/Pushover message; add a ≥ 5 alert line.

### Step 1: Write the failing test

```python
"""Phase 1: when an accepted-verdict notification fires, include the count of
hypotheses currently pending promotion + an alert line at ≥ 5."""

import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.hypothesis_runner import _count_pending_promotions, _pending_alert_line


def _make_hyp(tmp_path, hid, status):
    p = tmp_path / f"hyp_{hid}.json"
    p.write_text(json.dumps({"id": hid, "status": status}))
    return p


def test_count_pending_returns_only_accepted(tmp_path):
    _make_hyp(tmp_path, "001", "proposed")
    _make_hyp(tmp_path, "002", "accepted")
    _make_hyp(tmp_path, "003", "accepted")
    _make_hyp(tmp_path, "004", "rejected")
    _make_hyp(tmp_path, "005", "inconclusive")
    assert _count_pending_promotions(str(tmp_path)) == 2


def test_count_returns_zero_for_empty_dir(tmp_path):
    assert _count_pending_promotions(str(tmp_path)) == 0


def test_count_returns_zero_for_missing_dir():
    assert _count_pending_promotions("/nonexistent/path/that/does/not/exist") == 0


def test_alert_line_below_threshold_is_empty():
    assert _pending_alert_line(4) == ""


def test_alert_line_at_or_above_threshold_warns():
    line = _pending_alert_line(5)
    assert "⚠️" in line
    assert "5" in line
    line10 = _pending_alert_line(10)
    assert "10" in line10
```

### Step 2: Run, verify FAILS (helpers don't exist).

### Step 3: Add the helpers at module level in `learning/hypothesis_runner.py`

Near the top of the file, AFTER the existing constants (after `MIN_OOS_TRADES = 30` added in Task 5), add:

```python
PENDING_PROMOTION_ALERT_THRESHOLD = 5


def _count_pending_promotions(hyp_dir: str) -> int:
    """Count hypothesis files with status == 'accepted' (i.e. awaiting human promotion)."""
    if not os.path.isdir(hyp_dir):
        return 0
    n = 0
    for fn in os.listdir(hyp_dir):
        if not (fn.startswith("hyp_") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(hyp_dir, fn)) as f:
                spec = json.load(f)
            if spec.get("status") == "accepted":
                n += 1
        except Exception:
            continue   # malformed file — don't fail the count for that
    return n


def _pending_alert_line(count: int) -> str:
    """Return a one-line warning if pending promotions are at/above the threshold;
    empty string otherwise."""
    if count < PENDING_PROMOTION_ALERT_THRESHOLD:
        return ""
    return (f"\n⚠️ Promotion queue is **{count}** — consider a review session "
            f"before backlog grows.")
```

### Step 4: Update the accept-notification message to include the count

In `run()`, the `if verdict == "accepted" and self._post_fn:` block. Update it to:

```python
        if verdict == "accepted" and self._post_fn:
            try:
                hyp_dir = os.path.join(config.LOG_DIR, "learning", "hypotheses")
                pending = _count_pending_promotions(hyp_dir)
                self._post_fn(
                    f"**Hypothesis accepted: {spec.get('id')}**\n"
                    f"{spec.get('module')}.{spec.get('var')}: "
                    f"{spec.get('current_value')} → {spec.get('proposed_value')}\n"
                    f"OOS ΔSharpe {deltas['oos_sharpe_delta']:+.2f} · "
                    f"OOS ΔP&L {deltas['oos_pnl_delta']:+,} "
                    f"(n={modified['oos']['trades']} OOS trades)\n\n"
                    f"Apply with: python -m learning.promote {spec.get('id')}\n"
                    f"Pending promotions in queue: **{pending}**"
                    f"{_pending_alert_line(pending)}"
                )
            except Exception as e:
                logger.warning(f"HypothesisRunner: accept notify failed: {e}")
```

### Step 5: Run, verify all 5 PASS.

### Step 6: Commit

```bash
git add learning/hypothesis_runner.py tests/test_pending_promotion_count.py
git commit -m "feat: pending-promotion count + ≥5 alert line in hypothesis-runner notification"
```

---

## Task 7: All-time daily history refresh

**Files:**
- Modify: `requirements.txt` (add `yfinance==0.2.50`)
- Create: `backtests/refresh_all_history.py`
- Test: `tests/test_refresh_all_history.py`

A one-shot script that pulls free daily history from `yfinance`, CBOE, and FRED into local CSVs. The new files supplement (do not replace) `backtests/spy_history.csv` so existing harnesses keep working. The intent is a one-time `python -m backtests.refresh_all_history` run; the script is idempotent (re-runs overwrite).

### Step 1: Add yfinance to requirements + install

In `requirements.txt`, add a line at the end:

```
yfinance==0.2.50
```

Install it: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/pip install yfinance==0.2.50`
Verify import: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -c "import yfinance; print(yfinance.__version__)"`
Expected: `0.2.50`

### Step 2: Write the failing test — `tests/test_refresh_all_history.py`

```python
"""Phase 1: refresh_all_history is a one-shot script that writes daily CSVs.

We unit-test the *shape* — that the writer functions emit standardized
columns and the script lives where we expect — without making network calls.
Live integration is a manual `python -m backtests.refresh_all_history` run.
"""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from backtests.refresh_all_history import (
    OUT_DIR, OHLC_COLS, normalize_ohlc_frame, normalize_series_frame,
    SPY_LIKE_TICKERS, CBOE_VIX_FAMILY, FRED_SERIES,
)


def test_targets_are_declared():
    # The script declares which tickers/series it fetches. Sanity-check shape.
    assert "SPY" in SPY_LIKE_TICKERS
    assert "QQQ" in SPY_LIKE_TICKERS
    assert "XLK" in SPY_LIKE_TICKERS
    assert "TLT" in SPY_LIKE_TICKERS
    assert "VIX" in CBOE_VIX_FAMILY
    assert "VVIX" in CBOE_VIX_FAMILY
    assert "DGS10" in FRED_SERIES        # 10Y yield
    assert "DGS2"  in FRED_SERIES        # 2Y yield


def test_normalize_ohlc_frame_standardizes_columns():
    raw = pd.DataFrame({
        "Open": [100.0], "High": [102.0], "Low": [99.0],
        "Close": [101.0], "Volume": [1000000],
    }, index=pd.to_datetime(["2025-01-02"]))
    out = normalize_ohlc_frame(raw)
    assert list(out.columns) == OHLC_COLS
    assert out.index.name == "date"
    assert out.iloc[0]["open"] == 100.0


def test_normalize_series_frame_returns_date_value():
    raw = pd.DataFrame({"VALUE": [17.5, 18.0]},
                       index=pd.to_datetime(["2025-01-02", "2025-01-03"]))
    out = normalize_series_frame(raw, value_col="VALUE")
    assert list(out.columns) == ["value"]
    assert out.index.name == "date"
    assert out.iloc[0]["value"] == 17.5


def test_out_dir_is_under_backtests():
    # Files live alongside spy_history.csv; doesn't touch the existing file.
    assert OUT_DIR.endswith("backtests")
```

### Step 3: Run, verify FAILS (module doesn't exist).

### Step 4: Create `backtests/refresh_all_history.py`

```python
"""
backtests/refresh_all_history.py -- One-shot daily history refresh.

Pulls free daily data from three sources:
  - yfinance (SPY + family + sector ETFs + bonds + commodities)
  - CBOE (VIX, VIX9D, VIX3M, VIX6M, VVIX) via the same CSV pattern data/vix_client
    already uses for the daily VIX fallback
  - FRED (10Y / 2Y / 3M yields, fed funds) via the existing FRED API key

All outputs are CSVs under `backtests/` with standardized columns:
  - OHLC frames: date, open, high, low, close, volume
  - Single-series (VIX / yields): date, value

The existing `backtests/spy_history.csv` is NOT overwritten — yfinance writes
to `spy_history_yf.csv`. Phase 2+ harnesses opt into the deeper history;
existing harnesses keep their current source.

Run:
    python -m backtests.refresh_all_history
    python -m backtests.refresh_all_history --skip-fred   # if FRED key unavailable
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from typing import Iterable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config

OUT_DIR  = os.path.join(os.path.dirname(__file__))  # backtests/
OHLC_COLS = ["open", "high", "low", "close", "volume"]

# What we fetch — declared up front so tests can verify shape without network.
SPY_LIKE_TICKERS = [
    # Core
    "SPY", "QQQ", "IWM",
    # Sector ETFs (XLK matters most per recent KB; full set anyway)
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # Bonds / rates / FX
    "TLT", "IEF", "HYG", "UUP",
    # Commodities (sometimes correlated)
    "GLD", "USO",
]

# CBOE-hosted CSVs (same source pattern as data/vix_client's VIX fallback).
# Endpoints follow https://cdn.cboe.com/api/global/us_indices/daily_prices/<name>_History.csv
CBOE_VIX_FAMILY = {
    "VIX":   "VIX_History.csv",
    "VIX9D": "VIX9D_History.csv",
    "VIX3M": "VIX3M_History.csv",
    "VIX6M": "VIX6M_History.csv",
    "VVIX":  "VVIX_History.csv",
}
CBOE_URL_PREFIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/"

# FRED series IDs.
FRED_SERIES = {
    "DGS10": "10y_yield",   # 10-year Treasury constant maturity
    "DGS2":  "2y_yield",
    "DGS3MO": "3m_yield",
    "DFF":   "fed_funds",
}


# ── Normalizers ──────────────────────────────────────────────────────────────

def normalize_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance returns Open/High/Low/Close/Volume; standardize to lowercase
    and index name 'date'."""
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    out = out[[c for c in OHLC_COLS if c in out.columns]]
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    return out


def normalize_series_frame(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """A single-series source (VIX, yield) → date, value."""
    out = df.copy()
    if value_col in out.columns:
        out = out[[value_col]]
        out.columns = ["value"]
    else:
        # First non-index column.
        c = out.columns[0]
        out = out[[c]]
        out.columns = ["value"]
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    return out


# ── Fetchers ─────────────────────────────────────────────────────────────────

def fetch_yfinance_ohlc(ticker: str, start: str = "1993-01-01") -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — pip install yfinance==0.2.50")
        return None
    try:
        df = yf.download(ticker, start=start, progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        # yfinance sometimes returns multi-index columns; flatten if so.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return normalize_ohlc_frame(df)
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {e}")
        return None


def fetch_cboe_csv(filename: str) -> pd.DataFrame | None:
    import requests
    url = CBOE_URL_PREFIX + filename
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        # CBOE format: DATE,OPEN,HIGH,LOW,CLOSE — we want CLOSE as value.
        df.columns = [c.strip().lower() for c in df.columns]
        date_col = next((c for c in df.columns if c in ("date", "trade date")), df.columns[0])
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col]).set_index(date_col)
        # Prefer CLOSE; fall back to first numeric column.
        value_col = "close" if "close" in df.columns else df.select_dtypes(include="number").columns[0]
        return normalize_series_frame(df.rename(columns={value_col: "value"}), value_col="value")
    except Exception as e:
        logger.warning(f"CBOE fetch failed for {filename}: {e}")
        return None


def fetch_fred_series(series_id: str) -> pd.DataFrame | None:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning(f"FRED_API_KEY not set — skipping {series_id}")
        return None
    import requests
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json"}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("observations", [])
        if not data:
            return None
        rows = []
        for o in data:
            try:
                v = float(o["value"])
            except (ValueError, TypeError):
                continue
            rows.append({"date": pd.Timestamp(o["date"]), "value": v})
        if not rows:
            return None
        return pd.DataFrame(rows).set_index("date").rename_axis("date")
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
        return None


# ── Writers ──────────────────────────────────────────────────────────────────

def write_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path)
    logger.info(f"wrote {path} ({len(df)} rows)")


# ── Orchestrator ─────────────────────────────────────────────────────────────

def refresh_all(*, skip_yf: bool = False, skip_cboe: bool = False,
                skip_fred: bool = False) -> dict:
    counts: dict[str, int] = {}

    if not skip_yf:
        for ticker in SPY_LIKE_TICKERS:
            df = fetch_yfinance_ohlc(ticker)
            if df is not None:
                # SPY gets a _yf suffix so we don't clobber the existing
                # spy_history.csv that the live backtest expects.
                out_name = f"{ticker.lower()}_history{'_yf' if ticker == 'SPY' else ''}.csv"
                write_csv(df, os.path.join(OUT_DIR, out_name))
                counts[ticker] = len(df)
            time.sleep(0.5)   # be polite to Yahoo

    if not skip_cboe:
        for name, fn in CBOE_VIX_FAMILY.items():
            df = fetch_cboe_csv(fn)
            if df is not None:
                write_csv(df, os.path.join(OUT_DIR, f"{name.lower()}_history.csv"))
                counts[name] = len(df)
            time.sleep(0.5)

    if not skip_fred:
        for series_id, nice in FRED_SERIES.items():
            df = fetch_fred_series(series_id)
            if df is not None:
                write_csv(df, os.path.join(OUT_DIR, f"{nice}_history.csv"))
                counts[series_id] = len(df)
            time.sleep(0.5)

    return counts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skip-yf",   action="store_true")
    p.add_argument("--skip-cboe", action="store_true")
    p.add_argument("--skip-fred", action="store_true")
    args = p.parse_args()
    counts = refresh_all(skip_yf=args.skip_yf, skip_cboe=args.skip_cboe, skip_fred=args.skip_fred)
    print("\nrefresh summary:")
    for k, n in counts.items():
        print(f"  {k:<8s} {n:>6,d} rows")


if __name__ == "__main__":
    main()
```

### Step 5: Run, verify all 4 tests PASS

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_refresh_all_history.py -v`
Expected: 4 passed.

### Step 6: Run the script for real (network call) — manual verification

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m backtests.refresh_all_history --skip-fred`
(Skipping FRED is safe if `FRED_API_KEY` isn't set; CBOE + yfinance should both work.)
Expected: prints a summary like:
```
refresh summary:
  SPY      ~8000 rows
  QQQ      ~6700 rows
  ...
  VIX      ~9100 rows
  VVIX     ~5400 rows
```
And CSV files appear under `backtests/` for each ticker/series.

If yfinance fails for any ticker, log warning + continue (the script is already coded that way). Verify at least SPY succeeded.

### Step 7: Commit

```bash
git add requirements.txt backtests/refresh_all_history.py tests/test_refresh_all_history.py
git commit -m "feat: all-time daily history refresh (yfinance + CBOE + FRED)

One-shot script: pulls SPY/family/sector ETFs/bonds back to 1993 from
yfinance, VIX family from CBOE CSVs, yield curve + fed funds from FRED.
Writes to backtests/ alongside the existing spy_history.csv (which is
untouched). Phase 2+ harnesses opt into the deeper history; existing
backtests keep their current source.

Unlocks ~6× the daily-sample size (1993→2026 vs 2021→2026) for any
strategy that wants to validate across the 2000/2008/COVID regimes."
```

### Step 8: Don't commit the generated CSV files

The new CSVs (e.g. `spy_history_yf.csv`, `vix_history.csv`) are data artifacts. Add to `.gitignore`:

```
# Refreshed-history CSVs (generated by backtests/refresh_all_history.py)
backtests/spy_history_yf.csv
backtests/qqq_history.csv
backtests/iwm_history.csv
backtests/xlk_history.csv
backtests/xlf_history.csv
backtests/xle_history.csv
backtests/xly_history.csv
backtests/xlv_history.csv
backtests/xli_history.csv
backtests/xlp_history.csv
backtests/xlu_history.csv
backtests/xlb_history.csv
backtests/xlre_history.csv
backtests/xlc_history.csv
backtests/tlt_history.csv
backtests/ief_history.csv
backtests/hyg_history.csv
backtests/uup_history.csv
backtests/gld_history.csv
backtests/uso_history.csv
backtests/vix_history.csv
backtests/vix9d_history.csv
backtests/vix3m_history.csv
backtests/vix6m_history.csv
backtests/vvix_history.csv
backtests/10y_yield_history.csv
backtests/2y_yield_history.csv
backtests/3m_yield_history.csv
backtests/fed_funds_history.csv
```

Commit the gitignore update:

```bash
git add .gitignore
git commit -m "chore: gitignore generated history CSVs from refresh_all_history"
```

---

## Self-Review

**Spec coverage:** Every Phase-1 item from the strategic to-do list (#21, #11, #12, #16, #20, #25, #6) has a task. Mapping:
- Task 1 → to-do #21
- Task 2 → to-do #11
- Task 3 → to-do #12
- Task 4 → to-do #16
- Task 5 → to-do #20 (correctness fix)
- Task 6 → to-do #25
- Task 7 → to-do #6

**Placeholder scan:** No "TBD", "TODO", "implement later". Every code block is complete. The one fixture I'm relying on (`tmp_path` from pytest) is standard.

**Type consistency:** `_deltas(baseline, modified) -> dict` is a static method on `HypothesisRunner` (Task 5). `_verdict(deltas, modified) -> str` takes both args (Task 5). `_count_pending_promotions(hyp_dir: str) -> int` and `_pending_alert_line(count: int) -> str` are module-level helpers (Task 6). `KBEntry.strategy / dte_bucket / book` all `str | None = None` (Task 4). `KnowledgeBase.search(*, strategy=None, dte_bucket=None, book=None, category=None, days=None)` is keyword-only (Task 4). All consistent across tasks.

**Dependency order:** 1 (model pin) → 2 (config constants) → 3 (TUNABLE_PARAMS uses constants from Task 2) → 4 (KB tags, independent) → 5 (walk-forward, independent) → 6 (promotion count, depends on Task 5 for the notification block) → 7 (data refresh, independent). 1, 4, 7 can be parallelized via subagent dispatch (serial works fine).
