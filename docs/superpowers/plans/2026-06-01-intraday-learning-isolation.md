# Intraday Learning Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate intraday learning per sub-strategy and make it falsificationist — a dual-book exit-feasibility router (learning book = falsification sandbox) plus a per-sub-strategy reflector that runs a disconfirmation pass.

**Architecture:** A pure `assign_book` predicate (per-`(strategy,dte_bucket)` config thresholds) runs at the scanner seam after real pricing, routing marginal entries to the learning book. The reflector is refactored to extract a single `_reflect_one(...)` reflection unit, then loop it once per *active* sub-strategy (across both books, with a disconfirmation pass), falling back to one standby reflection on no-trade days. KB entries gain a `stance` tag.

**Tech Stack:** Python 3.11, pytest, loguru. Reuses `learning/exit_manager`, `learning/knowledge_base`, `learning/reflector`, `signals/intraday_structure_builder`, `scanners/intraday_scanner`.

**Spec:** `docs/superpowers/specs/2026-06-01-intraday-learning-isolation-design.md`

---

## Reference facts (verified — do not re-derive)

- **`KBEntry`** (`learning/knowledge_base.py`) is a dataclass already carrying `date, category, claim, evidence, confidence, source, tags, strategy, dte_bucket, book, id`. It is serialized to/from `knowledge.jsonl`. We ADD ONE field: `stance`. (The spec's `sub_strategy` is redundant — `strategy`+`dte_bucket` already exist.)
- **`learning/exit_manager._exit_rule_for(strategy, dte_bucket)`** is private, returns a dict including `"profit_target_pct": float`. We add a public `exit_rule_for(...)` wrapper.
- **`PaperBroker.execute_signal(setup)`** reads `setup["book"]` (default "disciplined"), applies `MAX_CONCURRENT_LEARNING=6` vs `MAX_CONCURRENT_DISCIPLINED=3`, records `book`. No change needed.
- **`scanners/intraday_scanner.py`** Phase-3 block: `enriched = build_intraday_structure(sd, spot=spy_spot, chain=chain)` then `broker.execute_signal(enriched)`. `enriched` has `strategy, dte_bucket, max_profit, max_loss, book` (book="disciplined" from the router default).
- **`Reflector.reflect_today`** (`learning/reflector.py`) flow: `_build_context` → `_build_prompt` → `_gather_anomaly_facts` → `_call_claude` (phi4_first normal / sonnet anomalous) → `_parse_reply` → `validate_kb_entries` → `_save_markdown` → KB append loop → optional `post`. `_build_context` returns `{date, prediction, plan, open_positions, recent_kb, rolling_accuracy}` where `rolling_accuracy = self.preds.accuracy(n=30, by_substrategy=True)`.
- **`PredictionLog.accuracy(n, by_substrategy=True)`** returns a dict keyed by `"{strategy}:{dte_bucket}:{book}"`.
- **`TradeRecorder.get_all_trades()`** → list of trade dicts; each has `strategy`, `dte_bucket`, `book`, `entry_date` (e.g. `"2026-05-18 09:16 AM EST"`), `outcome`.
- `is_auto_paper(t)` (in `learning.paper_broker`) identifies bot trades.
- Router sub-strategies: `call_debit_spread`, `put_debit_spread`, `iron_condor`; buckets `0DTE`, `1-3DTE`. Daily play combos also exist (e.g. `iron_condor`/`credit_spread` × `45DTE`).

**Pre-flight (run once before Task 1):**
```bash
cd /home/nexus/Projects/stock-market-trading-assistant
source .venv/bin/activate
pytest tests/ -k "knowledge or reflector or exit_manager or paper_broker or scanner_structure or feasibility" -p no:cacheprovider -q | tail -3
git status --short   # clean; create branch: git checkout -b intraday-learning-isolation
```

---

## Task 1: `stance` field on `KBEntry`

**Files:**
- Modify: `learning/knowledge_base.py` (the `KBEntry` dataclass)
- Test: `tests/test_knowledge_base.py` (existing; add tests — if absent, create with the standard sys.path header)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_knowledge_base.py`:
```python
def test_kbentry_stance_field_roundtrips(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.knowledge_base import KnowledgeBase, KBEntry
    kb = KnowledgeBase()
    eid = kb.append(KBEntry(date="2026-06-01", category="other", claim="x",
                            strategy="iron_condor", dte_bucket="0DTE",
                            book="learning", stance="disconfirming"))
    rows = kb.recent(days=3650)
    row = [r for r in rows if r["id"] == eid][0]
    assert row["stance"] == "disconfirming"


def test_kbentry_stance_defaults_none():
    from learning.knowledge_base import KBEntry
    assert KBEntry(date="2026-06-01", category="other", claim="x").stance is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_base.py -k stance -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'stance'`

- [ ] **Step 3: Write minimal implementation**

In `learning/knowledge_base.py`, add the field to `KBEntry` right after `book`:
```python
    book:       str | None     = None   # "disciplined" / "learning"
    stance:     str | None     = None   # "confirming" / "disconfirming" — falsification tag
```
The KB already serializes via dataclass→dict (e.g. `asdict`/`__dict__`); if `append` builds the row explicitly, add `"stance": entry.stance` there. Verify by reading `append` and `recent` — if they use `dataclasses.asdict(entry)` no further change is needed; if they list fields manually, add `stance`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_base.py -k stance -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add learning/knowledge_base.py tests/test_knowledge_base.py
git commit -m "feat: add stance field to KBEntry for falsification tagging"
```

---

## Task 2: public `exit_rule_for` accessor

**Files:**
- Modify: `learning/exit_manager.py`
- Test: `tests/test_learning_exit_manager.py` (existing)

- [ ] **Step 1: Write the failing test**

```python
def test_public_exit_rule_for_exposes_profit_target():
    from learning.exit_manager import exit_rule_for
    rule = exit_rule_for("iron_condor", "0DTE")
    assert "profit_target_pct" in rule
    assert isinstance(rule["profit_target_pct"], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_learning_exit_manager.py -k public_exit_rule_for -q`
Expected: FAIL — `cannot import name 'exit_rule_for'`

- [ ] **Step 3: Write minimal implementation**

In `learning/exit_manager.py`, add next to `_exit_rule_for`:
```python
def exit_rule_for(strategy: str | None, dte_bucket: str | None) -> dict:
    """Public accessor for the per-(strategy, dte_bucket) exit rule."""
    return _exit_rule_for(strategy, dte_bucket)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_learning_exit_manager.py -k public_exit_rule_for -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add learning/exit_manager.py tests/test_learning_exit_manager.py
git commit -m "feat: public exit_rule_for accessor"
```

---

## Task 3: `INTRADAY_FEASIBILITY` config + `assign_book` predicate

**Files:**
- Modify: `config.py` (add the dict near the other intraday constants, e.g. after `INTRADAY_PER_COMBO_DAILY_CAP`)
- Create: `signals/exit_feasibility.py`
- Test: `tests/test_exit_feasibility.py` (new)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_exit_feasibility.py -q`
Expected: FAIL — `No module named 'signals.exit_feasibility'`

- [ ] **Step 3: Write minimal implementation**

Add to `config.py`:
```python
# Per-(strategy, dte_bucket) exit-feasibility thresholds for dual-book routing.
# Entries clearing BOTH thresholds → disciplined book; else → learning book
# (the falsification sandbox). DEFAULT PERMISSIVE (all 0.0 → everything
# disciplined) until the intraday WF calibration populates real values —
# mirrors the router_wf MIN_* deferred-threshold pattern.
INTRADAY_FEASIBILITY = {
    ("call_debit_spread", "0DTE"):   {"min_target_dollars": 0.0, "min_rr": 0.0},
    ("call_debit_spread", "1-3DTE"): {"min_target_dollars": 0.0, "min_rr": 0.0},
    ("put_debit_spread",  "0DTE"):   {"min_target_dollars": 0.0, "min_rr": 0.0},
    ("put_debit_spread",  "1-3DTE"): {"min_target_dollars": 0.0, "min_rr": 0.0},
    ("iron_condor",       "0DTE"):   {"min_target_dollars": 0.0, "min_rr": 0.0},
    ("iron_condor",       "1-3DTE"): {"min_target_dollars": 0.0, "min_rr": 0.0},
}
```

Create `signals/exit_feasibility.py`:
```python
"""signals/exit_feasibility.py -- dual-book routing predicate.

Routes a priced intraday entry to the disciplined book (the real-money proxy) or
the learning book (the falsification sandbox: the trades disciplined refuses,
taken in paper to gather disconfirming evidence). See
docs/superpowers/specs/2026-06-01-intraday-learning-isolation-design.md.
"""
from __future__ import annotations

import config

_PERMISSIVE = {"min_target_dollars": 0.0, "min_rr": 0.0}


def assign_book(strategy, dte_bucket, max_profit, max_loss, *, profit_target_pct) -> str:
    """Return "disciplined" or "learning" for a priced entry.

    Disciplined iff the sub-strategy's profit target is a meaningful dollar
    amount (profit_target_pct * max_profit >= min_target_dollars) AND the reward/
    risk (max_profit / max_loss) >= min_rr. Otherwise learning. Total function:
    an unconfigured combo uses a permissive default (disciplined); never raises.
    """
    th = config.INTRADAY_FEASIBILITY.get((strategy, dte_bucket), _PERMISSIVE)
    target = (profit_target_pct or 0.0) * (max_profit or 0.0)
    rr = (max_profit / max_loss) if max_loss and max_loss > 0 else 0.0
    if target >= th["min_target_dollars"] and rr >= th["min_rr"]:
        return "disciplined"
    return "learning"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_exit_feasibility.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add config.py signals/exit_feasibility.py tests/test_exit_feasibility.py
git commit -m "feat: assign_book exit-feasibility predicate + INTRADAY_FEASIBILITY config"
```

---

## Task 4: Wire `assign_book` at the scanner seam

**Files:**
- Modify: `scanners/intraday_scanner.py` (the Phase-3 block + the `build_intraday_structure` helper area)
- Test: `tests/test_intraday_scanner_structure.py` (existing)

> **Note for implementer:** read the existing Phase-3 block. `enriched` has `strategy`, `dte_bucket`, `max_profit`, `max_loss`. Set `enriched["book"]` from `assign_book` BEFORE `broker.execute_signal(enriched)`, using the ExitManager's profit target so feasibility matches the real exit rule. Keep the gate + try/except intact.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_intraday_scanner_structure.py`:
```python
def test_build_intraday_structure_keeps_pricing_fields_for_book_assignment():
    # enriched must expose strategy/dte_bucket/max_profit/max_loss so the seam
    # can call assign_book. (Guards the fields the scanner reads.)
    setup = {"strategy": "iron_condor", "dte_bucket": "0DTE", "direction": "neutral"}
    chain = _chain([
        {"type": "put",  "strike": 497.0, "mid": 1.20, "mark": 1.20, "expiration": "2026-06-01"},
        {"type": "put",  "strike": 492.0, "mid": 0.40, "mark": 0.40, "expiration": "2026-06-01"},
        {"type": "call", "strike": 503.0, "mid": 1.10, "mark": 1.10, "expiration": "2026-06-01"},
        {"type": "call", "strike": 508.0, "mid": 0.35, "mark": 0.35, "expiration": "2026-06-01"},
    ])
    from datetime import date
    enriched = build_intraday_structure(setup, spot=500.0, chain=chain, as_of=date(2026, 6, 1))
    for k in ("strategy", "dte_bucket", "max_profit", "max_loss"):
        assert k in enriched
```

Add a new test file `tests/test_intraday_scanner_book.py` proving the seam assigns book:
```python
import os, sys
from datetime import date
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_assign_book_applied_at_seam(monkeypatch):
    """The scanner computes book from assign_book on the enriched setup."""
    import config
    # Force the iron_condor/0DTE combo to demand a large target → learning.
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "0DTE"): {"min_target_dollars": 1e9, "min_rr": 0.0}})
    from scanners.intraday_scanner import _assign_book_for_enriched
    enriched = {"strategy": "iron_condor", "dte_bucket": "0DTE",
                "max_profit": 100.0, "max_loss": 100.0}
    assert _assign_book_for_enriched(enriched) == "learning"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_scanner_book.py -q`
Expected: FAIL — `cannot import name '_assign_book_for_enriched'`

- [ ] **Step 3: Write minimal implementation**

In `scanners/intraday_scanner.py`, add a small helper near `build_intraday_structure`:
```python
def _assign_book_for_enriched(enriched: dict) -> str:
    """Route a priced enriched setup to the disciplined or learning book."""
    from signals.exit_feasibility import assign_book
    from learning.exit_manager import exit_rule_for
    pt = exit_rule_for(enriched.get("strategy"), enriched.get("dte_bucket"))["profit_target_pct"]
    return assign_book(enriched.get("strategy"), enriched.get("dte_bucket"),
                       enriched.get("max_profit"), enriched.get("max_loss"),
                       profit_target_pct=pt)
```
Then in the Phase-3 loop, right after `enriched = build_intraday_structure(...)` and the `if enriched is None: ... continue` guard, before `broker.execute_signal(enriched)`:
```python
                    enriched["book"] = _assign_book_for_enriched(enriched)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_scanner_book.py tests/test_intraday_scanner_structure.py -q && python -c "import scanners.intraday_scanner"`
Expected: PASS + clean import.

- [ ] **Step 5: Commit**

```bash
git add scanners/intraday_scanner.py tests/test_intraday_scanner_book.py tests/test_intraday_scanner_structure.py
git commit -m "feat: scanner assigns dual-book at the execute_signal seam"
```

---

## Task 5: Reflector — active-set + scoped-context helpers

Extract two pure-ish helpers so the orchestration in Task 7 is simple and testable.

**Files:**
- Modify: `learning/reflector.py`
- Test: `tests/test_reflector_substrategy.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reflector_substrategy.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _trade(strategy, dte_bucket, book, entry_date, outcome="open"):
    return {"strategy": strategy, "dte_bucket": dte_bucket, "book": book,
            "entry_date": entry_date, "outcome": outcome,
            "notes_entry": "[AUTO-PAPER] x", "source": "auto-paper"}


def test_active_substrategies_from_today(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    trades = [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("iron_condor", "0DTE", "learning",    "2026-06-01 09:35 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "disciplined", "2026-06-01 10:00 AM EST"),
        _trade("iron_condor", "45DTE", "disciplined", "2026-05-18 09:16 AM EST"),  # not today
    ]
    active = r._active_substrategies(trades, "2026-06-01")
    assert active == {("iron_condor", "0DTE"), ("call_debit_spread", "1-3DTE")}


def test_scoped_context_includes_both_books(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    trades = [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("iron_condor", "0DTE", "learning",    "2026-06-01 09:35 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "disciplined", "2026-06-01 10:00 AM EST"),
    ]
    ctx = r._build_substrategy_context("iron_condor", "0DTE", trades,
                                       accuracy={"iron_condor:0DTE:disciplined": {"n": 1},
                                                 "iron_condor:0DTE:learning": {"n": 1},
                                                 "call_debit_spread:1-3DTE:disciplined": {"n": 1}},
                                       today_str="2026-06-01")
    # only this combo's trades, both books
    assert len(ctx["trades"]) == 2
    assert set(ctx["accuracy"].keys()) == {"iron_condor:0DTE:disciplined", "iron_condor:0DTE:learning"}
    assert ctx["strategy"] == "iron_condor" and ctx["dte_bucket"] == "0DTE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflector_substrategy.py -q`
Expected: FAIL — `'Reflector' object has no attribute '_active_substrategies'`

- [ ] **Step 3: Write minimal implementation**

Add to `learning/reflector.py` (inside `Reflector`):
```python
    @staticmethod
    def _active_substrategies(trades: list[dict], today_str: str) -> set:
        """(strategy, dte_bucket) combos with an AUTO-PAPER trade entered today."""
        active = set()
        for t in trades:
            if not is_auto_paper(t):
                continue
            if (t.get("entry_date") or "")[:10] != today_str:
                continue
            strat, bucket = t.get("strategy"), t.get("dte_bucket")
            if strat and bucket:
                active.add((strat, bucket))
        return active

    @staticmethod
    def _build_substrategy_context(strategy, dte_bucket, trades, accuracy, today_str) -> dict:
        """Scoped context for ONE sub-strategy, across BOTH books."""
        combo_trades = [t for t in trades
                        if t.get("strategy") == strategy and t.get("dte_bucket") == dte_bucket]
        prefix = f"{strategy}:{dte_bucket}:"
        combo_acc = {k: v for k, v in (accuracy or {}).items() if k.startswith(prefix)}
        return {
            "date":       today_str,
            "strategy":   strategy,
            "dte_bucket": dte_bucket,
            "trades":     combo_trades[-10:],
            "accuracy":   combo_acc,
        }
```
(`is_auto_paper` is already imported in reflector.py.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reflector_substrategy.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add learning/reflector.py tests/test_reflector_substrategy.py
git commit -m "feat: reflector active-set + per-sub-strategy scoped context helpers"
```

---

## Task 6: Reflector — falsification prompt (disconfirmation pass)

**Files:**
- Modify: `learning/reflector.py` (`REFLECTOR_SYSTEM` and a per-sub-strategy prompt builder)
- Test: `tests/test_reflector_substrategy.py`

- [ ] **Step 1: Write the failing test**

```python
def test_substrategy_prompt_has_disconfirmation_and_scope(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector, REFLECTOR_SYSTEM
    # System prompt instructs a disconfirmation pass + stance on entries
    assert "disprove" in REFLECTOR_SYSTEM.lower() or "disconfirm" in REFLECTOR_SYSTEM.lower()
    assert "stance" in REFLECTOR_SYSTEM.lower()
    r = Reflector()
    ctx = {"date": "2026-06-01", "strategy": "iron_condor", "dte_bucket": "0DTE",
           "trades": [{"trade_id": "A", "book": "learning", "outcome": "open"}],
           "accuracy": {"iron_condor:0DTE:learning": {"n": 1}}}
    p = r._build_substrategy_prompt(ctx)
    assert "iron_condor" in p and "0DTE" in p
    assert "disprove" in p.lower() or "challenge" in p.lower()  # disconfirmation framing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflector_substrategy.py -k prompt -q`
Expected: FAIL — `cannot import name` / `no attribute '_build_substrategy_prompt'` / assertion on REFLECTOR_SYSTEM.

- [ ] **Step 3: Write minimal implementation**

In `learning/reflector.py`, extend `REFLECTOR_SYSTEM` — append this block before the closing `"""` (keep the existing JSON-shape instructions; add the new requirements and the `stance` key in the kb_entries shape):
```
After the "what worked" analysis you MUST run a DISCONFIRMATION PASS: actively
try to DISPROVE the current stance. Ask: what belief did today's data challenge?
What would have to be true for this sub-strategy's gate/threshold to be WRONG?
Compare the disciplined trades against the learning-book (refused) trades for the
same sub-strategy — did refusing the learning-book trades hold up, or would they
have won? Each kb_entry MUST include a "stance" field set to "confirming" or
"disconfirming".
```
Add the prompt builder:
```python
    def _build_substrategy_prompt(self, ctx: dict) -> str:
        return (
            f"DATE: {ctx['date']}\n"
            f"SUB-STRATEGY: {ctx['strategy']} @ {ctx['dte_bucket']} "
            f"(reflect on THIS sub-strategy only, across BOTH books)\n\n"
            f"THIS SUB-STRATEGY'S TRADES (both books):\n"
            f"{json.dumps(ctx['trades'], indent=2, default=str)}\n\n"
            f"ACCURACY SLICES (disciplined vs learning):\n"
            f"{json.dumps(ctx['accuracy'], indent=2)}\n\n"
            f"Run the what-worked analysis AND the disconfirmation pass. Did "
            f"refusing the learning-book trades hold up, or do they challenge our "
            f"gate? Produce the JSON reflection now."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reflector_substrategy.py -k prompt -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add learning/reflector.py tests/test_reflector_substrategy.py
git commit -m "feat: falsificationist reflector prompt (disconfirmation pass + stance)"
```

---

## Task 7: Reflector — restructure `reflect_today` (per-sub-strategy loop + standby)

Extract the existing single-reflection machinery into `_reflect_one(...)`, then loop it per active sub-strategy; fall back to one standby reflection when none active.

**Files:**
- Modify: `learning/reflector.py` (`reflect_today`, new `_reflect_one`, `_save_markdown`, KB append)
- Test: `tests/test_reflector_substrategy.py`; existing `tests/test_learning_reflector.py` must stay green.

> **Note for implementer:** Read `reflect_today` and `_save_markdown` fully first. Refactor so a single reflection unit — prompt → `_gather_anomaly_facts` → `_call_claude` → `_parse_reply` → `validate_kb_entries` → save MD → append KB entries — lives in `_reflect_one(prompt, scope, today_str, context)`. `scope` is a dict `{"strategy":..., "dte_bucket":..., "book":...}` (None values for the standby unit) used to stamp KB entries. Per-sub-strategy uses `book=None` on the entry (the entry pertains to the combo across both books; the LLM sets `stance`). Preserve ALL existing wiring (anomaly routing, kb_validator, route telemetry, optional `post`).

- [ ] **Step 1: Write the failing test**

```python
def test_reflect_today_runs_once_per_active_substrategy(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    # two active sub-strategies today
    monkeypatch.setattr(r.trades, "get_all_trades", lambda: [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "learning", "2026-06-01 10:00 AM EST"),
    ])
    calls = []
    def fake_reflect_one(prompt, scope, today_str, context):
        calls.append(scope)
        return {"kb_ids": [], "markdown": "x", "route": "phi4", "parsed": True}
    monkeypatch.setattr(r, "_reflect_one", fake_reflect_one)
    out = r.reflect_today(today=__import__("datetime").date(2026, 6, 1))
    scopes = {(s.get("strategy"), s.get("dte_bucket")) for s in calls}
    assert scopes == {("iron_condor", "0DTE"), ("call_debit_spread", "1-3DTE")}
    assert out["units"] == 2


def test_reflect_today_standby_when_no_active(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    monkeypatch.setattr(r.trades, "get_all_trades", lambda: [])  # no trades today
    calls = []
    monkeypatch.setattr(r, "_reflect_one",
                        lambda prompt, scope, today_str, context: calls.append(scope) or
                        {"kb_ids": [], "markdown": "x", "route": "phi4", "parsed": True})
    out = r.reflect_today(today=__import__("datetime").date(2026, 6, 1))
    assert len(calls) == 1
    assert calls[0].get("strategy") is None   # standby unit
    assert out["units"] == 1


def test_reflect_today_one_failure_does_not_sink_others(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from learning.reflector import Reflector
    r = Reflector()
    monkeypatch.setattr(r.trades, "get_all_trades", lambda: [
        _trade("iron_condor", "0DTE", "disciplined", "2026-06-01 09:30 AM EST"),
        _trade("call_debit_spread", "1-3DTE", "learning", "2026-06-01 10:00 AM EST"),
    ])
    def flaky(prompt, scope, today_str, context):
        if scope.get("strategy") == "iron_condor":
            raise RuntimeError("LLM boom")
        return {"kb_ids": ["k1"], "markdown": "x", "route": "phi4", "parsed": True}
    monkeypatch.setattr(r, "_reflect_one", flaky)
    out = r.reflect_today(today=__import__("datetime").date(2026, 6, 1))
    assert out["units"] == 2 and out["failed"] == 1 and "k1" in out["kb_ids"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflector_substrategy.py -k reflect_today -q`
Expected: FAIL — `reflect_today` returns the old shape (no `units`/`failed`), `_reflect_one` missing.

- [ ] **Step 3: Write minimal implementation**

Refactor `reflect_today` in `learning/reflector.py`:
```python
    def reflect_today(self, today: date | None = None) -> dict:
        today     = today or date.today()
        today_str = today.isoformat()
        all_trades = self.trades.get_all_trades()
        accuracy   = self.preds.accuracy(n=30, by_substrategy=True)
        active = self._active_substrategies(all_trades, today_str)

        units, kb_ids, failed = [], [], 0

        if active:
            for strategy, dte_bucket in sorted(active):
                ctx = self._build_substrategy_context(strategy, dte_bucket,
                                                      all_trades, accuracy, today_str)
                prompt = self._build_substrategy_prompt(ctx)
                scope = {"strategy": strategy, "dte_bucket": dte_bucket, "book": None}
                try:
                    res = self._reflect_one(prompt, scope, today_str, ctx)
                    units.append(res); kb_ids.extend(res.get("kb_ids", []))
                except Exception as e:
                    failed += 1
                    logger.exception(f"Reflector: sub-strategy {strategy}:{dte_bucket} failed: {e}")
        else:
            # Standby: prediction/skip/near-miss reflection (preserves the heartbeat).
            context = self._build_context(today_str)
            prompt  = self._build_prompt(context)
            scope   = {"strategy": None, "dte_bucket": None, "book": None}
            try:
                res = self._reflect_one(prompt, scope, today_str, context)
                units.append(res); kb_ids.extend(res.get("kb_ids", []))
            except Exception as e:
                failed += 1
                logger.exception(f"Reflector: standby reflection failed: {e}")

        if self.post and units:
            try:
                self.post(f"🪞 **Daily Reflection {today_str}** — "
                          f"{len(units)} unit(s), +{len(kb_ids)} KB entries")
            except Exception as e:
                logger.warning(f"Reflector: post_fn failed: {e}")

        return {"date": today_str, "units": len(units), "failed": failed, "kb_ids": kb_ids}
```
Extract `_reflect_one` from the OLD `reflect_today` body (prompt already built by caller):
```python
    def _reflect_one(self, prompt: str, scope: dict, today_str: str, context: dict) -> dict:
        facts = self._gather_anomaly_facts(context)
        reply, route = self._call_claude(prompt, facts)
        parsed, parse_err = self._parse_reply(reply)
        if parsed:
            from learning.kb_validator import validate_kb_entries
            parsed, _ = validate_kb_entries(
                parsed,
                facts={"trade_ids": self._extract_today_trade_ids(context),
                       "today_numbers": self._extract_today_numbers(context),
                       "kb_ids": self._extract_recent_kb_ids(context)},
                default_kind="daily",
            )
        label = (f"{scope['strategy']}__{scope['dte_bucket']}"
                 if scope.get("strategy") else "standby")
        md_path = self._save_markdown(today_str, parsed, reply, context, parse_err, label=label)
        kb_ids = []
        if parsed and parsed.get("kb_entries"):
            for raw in parsed["kb_entries"]:
                try:
                    entry = KBEntry(
                        date=today_str, category=raw.get("category", "other"),
                        claim=raw.get("claim", "")[:500], evidence=raw.get("evidence", "")[:1000],
                        confidence=float(raw.get("confidence", 0.5)), source="reflector",
                        tags=list(raw.get("tags") or [])[:8],
                        strategy=scope.get("strategy"), dte_bucket=scope.get("dte_bucket"),
                        book=scope.get("book"), stance=raw.get("stance"),
                    )
                    kb_ids.append(self.kb.append(entry))
                except Exception as e:
                    logger.warning(f"Reflector: skipping malformed KB entry: {e}")
        return {"kb_ids": kb_ids, "markdown": md_path, "route": route, "parsed": bool(parsed)}
```
Update `_save_markdown` signature to accept `label: str = "standby"` and write to a per-day dir when a sub-strategy: `logs/learning/reflections/<today_str>/<label>.md` (create the dir). When `label == "standby"`, keep the legacy path `logs/learning/reflections/<today_str>.md` for back-compat. Read the existing `_save_markdown` and adapt its path logic accordingly; keep its content formatting.

- [ ] **Step 4: Run tests to verify they pass (and existing reflector tests stay green)**

Run: `pytest tests/test_reflector_substrategy.py tests/test_learning_reflector.py tests/test_reflector_helpers.py tests/test_reflector_routing.py -q`
Expected: PASS. If `test_learning_reflector.py` asserts the OLD `reflect_today` return keys (e.g. `markdown`, `kb_ids`), update those assertions to the new shape (`units`, `failed`, `kb_ids`) — this is an intentional contract change, not a regression; confirm each changed assertion reflects the new per-unit design.

- [ ] **Step 5: Commit**

```bash
git add learning/reflector.py tests/test_reflector_substrategy.py tests/test_learning_reflector.py
git commit -m "feat: per-sub-strategy falsificationist reflect_today (loop + standby)"
```

---

## Final verification

- [ ] **Run the affected suite offline (host network flaky — avoid live-FRED):**

Run:
```bash
pytest tests/ -k "knowledge or reflector or exit_manager or exit_feasibility or paper_broker or scanner or structure or spy_daily" -p no:cacheprovider -q | tail -5
```
Expected: all green (only the pre-existing live-FRED `test_economic_scanner`/`test_fred` may fail if caught — note separately).

- [ ] **Update BUILD_LOG.md** with the entry (dual-book + falsificationist reflector; note INTRADAY_FEASIBILITY defaults permissive until calibration; deferred extension-gate shadow-test).

---

## Self-review (completed by author)

- **Spec coverage:** exit-feasibility predicate (T3) + per-combo config (T3) + scanner-seam routing (T4); `exit_rule_for` (T2); learning book = falsification sandbox (T3/T4 routing + the spec's framing); per-sub-strategy reflector across both books (T5/T6/T7); disconfirmation pass (T6); KB `stance` (T1) — note `sub_strategy` redundant since `strategy`/`dte_bucket` already exist; skip-day standby (T7); independent failure handling (T7). All spec sections map to a task.
- **Placeholder scan:** none — every code step has complete code; T4/T7 carry "read the surrounding file" notes because they edit large existing files, but the inserted code is complete.
- **Type consistency:** `assign_book(strategy, dte_bucket, max_profit, max_loss, *, profit_target_pct)` consistent T3→T4; `_active_substrategies`/`_build_substrategy_context`/`_build_substrategy_prompt`/`_reflect_one` signatures consistent T5→T7; `scope` dict shape `{strategy, dte_bucket, book}` consistent; KBEntry `stance` consistent T1→T7.
