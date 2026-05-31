# Router `route_explain()` + Decision Tracking Surface — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the offline half of the router decision-tracking surface — `route_explain()` plus a backfill and parquet rollup over cached 2024 history — so the router's gating behavior (accepted *and* rejected) is observable and the threshold-calibration exercise (spec 2026-05-29) has a full year of data.

**Architecture:** Add a non-mutating `route_explain()` beside `route()` in `signals/intraday_entry_router.py`, reusing the existing gate helpers (`_passes_entry_tier`, `_assign_dte_buckets`) and a new shared dedup primitive (`_dedup_partition`, which `_dedup_filter` is refactored to wrap, leaving `route()`'s behavior identical). A thin `learning/router_tracker.py` flattens a decision trace to an append-only JSONL row. `backtests/router_track_backfill.py` replays cached 2024 days through `route_explain()` (mirroring the walk-forward's per-day fresh-broker, 09:45-ET model); `backtests/router_track_rollup.py` compacts the JSONL to one parquet.

**Tech Stack:** Python 3.11, pandas + pyarrow (parquet), pytz (US/Eastern), loguru, pytest. Reuses `backtests/router_setup_builder.build_historical_setup` and `backtests/intraday_router_wf._MockBroker`.

**Spec:** `docs/superpowers/specs/2026-05-30-router-explain-tracking-surface-design.md`

**Scope note:** This plan is **Phase 1 only** (offline). Phase 2 (live trace at the scanner seam) is deferred per the user's offline-only decision and is not covered here.

**Pre-flight (run once before Task 1):**
```bash
cd /home/nexus/Projects/stock-market-trading-assistant
source .venv/bin/activate
pytest tests/ -v -m "not integration" --tb=short -q | tail -5   # confirm green baseline
git status --short                                              # must be clean
```

**Reference facts (verified, do not re-derive):**
- `SPYSetup` is a `@dataclass` in `signals/spy_options_engine.py`; required fields `strategy, conviction, timeframe, score, reasons`; optional `direction, trend` (among others).
- Config: `ENTRY_TIER_MINIMUM="high"`, `ULTRA_CONVICTION_DOUBLE_DTE_SCORE=85`, `INTRADAY_PER_COMBO_DAILY_CAP=2`, `INTRADAY_DTE_MORNING_CUTOFF="12:30"`, `LOG_DIR` exists (KB writes under `config.LOG_DIR/learning/`).
- `_MockBroker` (in `backtests/intraday_router_wf.py`): `get_trades_by(*, strategy, dte_bucket)`, `_entry_count_today_by_combo(strategy, dte_bucket)`, `record_open(*, strategy, dte_bucket)`, and `self.trades = self`. `record_open` always stamps `outcome="open"`.
- Existing router helpers: `_passes_entry_tier(setup)`, `_assign_dte_buckets(setup, now)`, `_dedup_filter(strategy, buckets, broker)`, `_build_setup_dict(setup, dte_bucket, now)`. Module already does `from datetime import datetime, time, timedelta`.

---

## Task 1: `_dedup_partition` shared primitive (refactor `_dedup_filter`)

Extract the dedup logic into one function that also returns rejection reasons, and make `_dedup_filter` a thin wrapper so `route()` is behaviorally unchanged.

**Files:**
- Modify: `signals/intraday_entry_router.py` (the `_dedup_filter` function, lines ~104-124)
- Test: `tests/test_route_explain.py` (new file; first tests land here)

- [x] **Step 1: Write the failing test**

Create `tests/test_route_explain.py`:

```python
import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.spy_options_engine import SPYSetup
from signals.intraday_entry_router import _dedup_partition, _dedup_filter
from backtests.intraday_router_wf import _MockBroker

ET = pytz.timezone("US/Eastern")


def _setup(strategy="iron_condor", conviction="high", score=70, direction="neutral", trend="range-bound"):
    return SPYSetup(strategy=strategy, conviction=conviction, timeframe="intraday",
                    score=score, reasons=["r1", "r2"], direction=direction, trend=trend)


def test_dedup_partition_all_clear_returns_buckets_and_no_rejects():
    broker = _MockBroker()
    allowed, rejected = _dedup_partition("iron_condor", ["0DTE", "1-3DTE"], broker)
    assert allowed == ["0DTE", "1-3DTE"]
    assert rejected == []


def test_dedup_partition_open_position_rejects_with_reason():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    allowed, rejected = _dedup_partition("iron_condor", ["0DTE", "1-3DTE"], broker)
    assert allowed == ["1-3DTE"]
    assert len(rejected) == 1
    bucket, reason = rejected[0]
    assert bucket == "0DTE"
    assert "open position" in reason and "iron_condor" in reason


def test_dedup_filter_still_returns_same_list_as_partition_allowed():
    # Regression: route() relies on _dedup_filter; it must equal partition's allowed.
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    allowed, _ = _dedup_partition("iron_condor", ["0DTE", "1-3DTE"], broker)
    assert _dedup_filter("iron_condor", ["0DTE", "1-3DTE"], broker) == allowed
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_route_explain.py -v`
Expected: FAIL — `ImportError: cannot import name '_dedup_partition'`.

- [x] **Step 3: Write minimal implementation**

In `signals/intraday_entry_router.py`, replace the existing `_dedup_filter` function (lines ~104-124) with the shared primitive plus a wrapper:

```python
def _dedup_partition(strategy: str, dte_buckets: list[str], broker
                     ) -> tuple[list[str], list[tuple[str, str]]]:
    """D rule, with reasons. Returns (allowed, [(bucket, reason), ...]).

    A bucket is dropped if a position is already open in (strategy, bucket)
    OR today's entry count for the combo has reached
    config.INTRADAY_PER_COMBO_DAILY_CAP. This is the single dedup
    implementation; _dedup_filter wraps it so route() is unchanged.
    """
    allowed: list[str] = []
    rejected: list[tuple[str, str]] = []
    for bucket in dte_buckets:
        open_in_combo = [
            t for t in broker.trades.get_trades_by(strategy=strategy, dte_bucket=bucket)
            if t.get("outcome") == "open"
        ]
        if open_in_combo:
            rejected.append((bucket, f"open position already in ({strategy}, {bucket})"))
            continue
        n_today = broker._entry_count_today_by_combo(strategy, bucket)
        if n_today >= config.INTRADAY_PER_COMBO_DAILY_CAP:
            rejected.append((bucket,
                f"per-combo daily cap reached ({n_today} >= {config.INTRADAY_PER_COMBO_DAILY_CAP})"))
            continue
        allowed.append(bucket)
    return allowed, rejected


def _dedup_filter(strategy: str, dte_buckets: list[str], broker) -> list[str]:
    """D rule: drop a bucket if a position is already open in (strategy, bucket)
    OR today's entry count for the combo has reached
    config.INTRADAY_PER_COMBO_DAILY_CAP. Thin wrapper over _dedup_partition so
    route()'s observable behavior is identical."""
    allowed, _ = _dedup_partition(strategy, dte_buckets, broker)
    return allowed
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_route_explain.py -v`
Expected: PASS (3 tests).

- [x] **Step 5: Run the router's existing tests to confirm `route()` unchanged**

Run: `pytest tests/test_intraday_entry_router.py tests/test_intraday_router_wf.py -v --tb=short`
Expected: PASS (all pre-existing tests still green — the refactor is behavior-preserving).

- [x] **Step 6: Commit**

```bash
git add signals/intraday_entry_router.py tests/test_route_explain.py
git commit -m "refactor: extract _dedup_partition; _dedup_filter wraps it"
```

---

## Task 2: `_dte_reject_detail` helper

A human-readable reason a DTE bucket was *not* assigned by `_assign_dte_buckets`.

**Files:**
- Modify: `signals/intraday_entry_router.py` (add function after `_assign_dte_buckets`)
- Test: `tests/test_route_explain.py`

- [x] **Step 1: Write the failing test**

Append to `tests/test_route_explain.py`:

```python
from signals.intraday_entry_router import _dte_reject_detail


def test_dte_reject_detail_friday_pm_drops_1_3dte():
    fri_pm = ET.localize(datetime(2024, 7, 12, 13, 0))   # 2024-07-12 is a Friday
    detail = _dte_reject_detail(_setup(), fri_pm, "1-3DTE")
    assert "Friday-PM safeguard" in detail


def test_dte_reject_detail_morning_drops_1_3dte():
    mon_am = ET.localize(datetime(2024, 7, 15, 10, 0))   # Monday 10:00 ET (pre-cutoff)
    detail = _dte_reject_detail(_setup(), mon_am, "1-3DTE")
    assert "morning" in detail


def test_dte_reject_detail_afternoon_drops_0dte():
    mon_pm = ET.localize(datetime(2024, 7, 15, 13, 0))   # Monday 13:00 ET (post-cutoff)
    detail = _dte_reject_detail(_setup(), mon_pm, "0DTE")
    assert "afternoon" in detail
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_route_explain.py -k dte_reject_detail -v`
Expected: FAIL — `ImportError: cannot import name '_dte_reject_detail'`.

- [x] **Step 3: Write minimal implementation**

In `signals/intraday_entry_router.py`, add directly after `_assign_dte_buckets` (ends ~line 101):

```python
def _dte_reject_detail(setup, now: datetime, bucket: str) -> str:
    """Human-readable reason `bucket` was not in _assign_dte_buckets's output."""
    cutoff_h, cutoff_m = (int(x) for x in config.INTRADAY_DTE_MORNING_CUTOFF.split(":"))
    is_friday    = now.weekday() == 4
    is_afternoon = now.time() >= time(cutoff_h, cutoff_m)
    if is_friday and is_afternoon and bucket == "1-3DTE":
        return "Friday-PM safeguard: 1-3DTE dropped (no weekend exposure)"
    if bucket == "0DTE":
        return "afternoon → 1-3DTE assigned, 0DTE not selected"
    return "morning → 0DTE assigned, 1-3DTE not selected"
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_route_explain.py -k dte_reject_detail -v`
Expected: PASS (3 tests).

- [x] **Step 5: Commit**

```bash
git add signals/intraday_entry_router.py tests/test_route_explain.py
git commit -m "feat: add _dte_reject_detail reason helper for route_explain"
```

---

## Task 3: `route_explain()`

The non-mutating decision trace. Must agree with `route()` on the accept set (tested invariant).

**Files:**
- Modify: `signals/intraday_entry_router.py` (add `route_explain` after `route`)
- Test: `tests/test_route_explain.py`

- [x] **Step 1: Write the failing test**

Append to `tests/test_route_explain.py`:

```python
from signals.intraday_entry_router import route, route_explain


class _CapBroker:
    """Broker whose combo has 0 OPEN trades but a today-count at the cap —
    isolates the dedup CAP branch (the open-position branch can't fire)."""
    def __init__(self):
        self.trades = self
    def get_trades_by(self, *, strategy, dte_bucket):
        return []
    def _entry_count_today_by_combo(self, strategy, dte_bucket):
        return 2   # == config.INTRADAY_PER_COMBO_DAILY_CAP


def _accepted_buckets(trace):
    return [a["dte_bucket"] for a in trace["accepted"]]


def test_route_explain_tier_fail():
    s = _setup(conviction="standard")               # below ENTRY_TIER_MINIMUM="high"
    now = ET.localize(datetime(2024, 7, 15, 10, 0))
    trace = route_explain(s, now, _MockBroker())
    assert trace["passed_tier"] is False
    assert trace["accepted"] == []
    assert len(trace["rejected"]) == 1
    assert trace["rejected"][0]["gate"] == "tier"


def test_route_explain_morning_high_accepts_0dte_rejects_1_3dte_on_dte():
    s = _setup(conviction="high", score=70)
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # Monday AM
    trace = route_explain(s, now, _MockBroker())
    assert _accepted_buckets(trace) == ["0DTE"]
    assert [r for r in trace["rejected"] if r["gate"] == "dte"]
    assert all(r["dte_bucket"] == "1-3DTE" for r in trace["rejected"] if r["gate"] == "dte")


def test_route_explain_ultra_conviction_accepts_both_no_rejects():
    s = _setup(conviction="high", score=90)           # >= ULTRA_CONVICTION_DOUBLE_DTE_SCORE
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # Monday (not Friday)
    trace = route_explain(s, now, _MockBroker())
    assert sorted(_accepted_buckets(trace)) == ["0DTE", "1-3DTE"]
    assert trace["rejected"] == []


def test_route_explain_dedup_open_position_rejects():
    s = _setup(strategy="iron_condor", conviction="high", score=70)
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # morning → candidate 0DTE
    trace = route_explain(s, now, broker)
    assert trace["accepted"] == []
    assert any(r["gate"] == "dedup" and r["dte_bucket"] == "0DTE" for r in trace["rejected"])


def test_route_explain_dedup_cap_rejects():
    s = _setup(strategy="iron_condor", conviction="high", score=70)
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # morning → candidate 0DTE
    trace = route_explain(s, now, _CapBroker())
    assert trace["accepted"] == []
    assert any(r["gate"] == "dedup" and "cap" in r["detail"] for r in trace["rejected"])


def test_route_explain_accept_set_matches_route_across_fixtures():
    cases = [
        (_setup(conviction="standard"),               ET.localize(datetime(2024, 7, 15, 10, 0))),
        (_setup(conviction="high", score=70),         ET.localize(datetime(2024, 7, 15, 10, 0))),
        (_setup(conviction="high", score=70),         ET.localize(datetime(2024, 7, 15, 13, 0))),
        (_setup(conviction="high", score=90),         ET.localize(datetime(2024, 7, 15, 10, 0))),
        (_setup(conviction="high", score=90),         ET.localize(datetime(2024, 7, 12, 13, 0))),  # Fri PM
    ]
    for s, now in cases:
        b1, b2 = _MockBroker(), _MockBroker()
        explain_accepted = [a["dte_bucket"] for a in route_explain(s, now, b1)["accepted"]]
        route_accepted   = [d["dte_bucket"] for d in route(s, now, b2)]
        assert explain_accepted == route_accepted, (s.conviction, s.score, now)
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_route_explain.py -k route_explain -v`
Expected: FAIL — `ImportError: cannot import name 'route_explain'`.

- [x] **Step 3: Write minimal implementation**

In `signals/intraday_entry_router.py`, add after `route` (ends ~line 165):

```python
def route_explain(setup, now: datetime, broker) -> dict:
    """Non-mutating decision trace for route()'s three-gate pipeline.

    Returns {passed_tier, candidate_buckets, accepted, rejected}. Reuses the
    same gate helpers route() uses, so the accept set is identical to route()'s
    output (verified by test_route_explain_accept_set_matches_route_*).
    """
    # Gate 1: entry tier.
    if not _passes_entry_tier(setup):
        return {
            "passed_tier": False,
            "candidate_buckets": [],
            "accepted": [],
            "rejected": [{
                "dte_bucket": None,
                "gate": "tier",
                "detail": f"conviction={setup.conviction} < minimum={config.ENTRY_TIER_MINIMUM}",
            }],
        }

    rejected: list[dict] = []

    # Gate 2: DTE assignment. Reasons inferred from the universe NOT assigned.
    candidate_buckets = _assign_dte_buckets(setup, now)
    universe = {"0DTE", "1-3DTE"}
    for b in sorted(universe - set(candidate_buckets)):
        rejected.append({"dte_bucket": b, "gate": "dte",
                         "detail": _dte_reject_detail(setup, now, b)})

    # Gate 3: dedup. Shared primitive returns allowed + reasons.
    allowed, dedup_rejects = _dedup_partition(setup.strategy, candidate_buckets, broker)
    for b, reason in dedup_rejects:
        rejected.append({"dte_bucket": b, "gate": "dedup", "detail": reason})

    return {
        "passed_tier": True,
        "candidate_buckets": candidate_buckets,
        "accepted": [{"dte_bucket": b} for b in allowed],
        "rejected": rejected,
    }
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_route_explain.py -v`
Expected: PASS (all tests in the file).

- [x] **Step 5: Commit**

```bash
git add signals/intraday_entry_router.py tests/test_route_explain.py
git commit -m "feat: add route_explain() decision trace for the entry router"
```

---

## Task 4: `learning/router_tracker.py` — flatten + write JSONL

**Files:**
- Create: `learning/router_tracker.py`
- Test: `tests/test_router_tracker.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_router_tracker.py`:

```python
import json
import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from signals.spy_options_engine import SPYSetup
from learning.router_tracker import build_trace_record, write_trace

ET = pytz.timezone("US/Eastern")


def _setup(**kw):
    base = dict(strategy="iron_condor", conviction="high", timeframe="intraday",
                score=72, reasons=["a"], direction="neutral", trend="range-bound")
    base.update(kw)
    return SPYSetup(**base)


def _trace():
    return {
        "passed_tier": True,
        "candidate_buckets": ["0DTE"],
        "accepted": [{"dte_bucket": "0DTE"}],
        "rejected": [{"dte_bucket": "1-3DTE", "gate": "dte", "detail": "morning ..."}],
    }


def test_build_trace_record_shape():
    now = ET.localize(datetime(2026, 5, 30, 9, 16))
    rec = build_trace_record(_setup(), _trace(), now, source="live")
    assert rec["ts"] == now.isoformat()
    assert rec["date"] == "2026-05-30"
    assert rec["source"] == "live"
    assert rec["strategy"] == "iron_condor"
    assert rec["conviction"] == "high"
    assert rec["score"] == 72
    assert rec["direction"] == "neutral"
    assert rec["trend"] == "range-bound"
    assert rec["passed_tier"] is True
    assert rec["accepted"] == ["0DTE"]
    assert rec["rejected"] == [{"dte_bucket": "1-3DTE", "gate": "dte", "detail": "morning ..."}]


def test_build_trace_record_handles_none_direction_and_trend():
    now = ET.localize(datetime(2026, 5, 30, 9, 16))
    rec = build_trace_record(_setup(direction=None, trend=None), _trace(), now, source="backfill")
    assert rec["direction"] is None
    assert rec["trend"] is None
    assert rec["source"] == "backfill"


def test_write_trace_appends_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    now = ET.localize(datetime(2026, 5, 30, 9, 16))
    rec = build_trace_record(_setup(), _trace(), now, source="live")
    path = write_trace(rec)
    assert path.endswith(os.path.join("learning", "router_track", "2026-05-30.jsonl"))
    write_trace(rec)                                  # second append
    with open(path) as f:
        lines = f.read().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == rec
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_router_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learning.router_tracker'`.

- [x] **Step 3: Write minimal implementation**

Create `learning/router_tracker.py`:

```python
"""learning/router_tracker.py -- Flatten a route_explain() decision trace to a
JSONL row and append it under logs/learning/router_track/.

One row per (setup, evaluation). The reflector reads these to narrate the
router's gating behavior; the rollup compacts them to parquet for calibration.
The full JSONL is the lossless source of truth.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


def build_trace_record(setup, trace: dict, now: datetime, source: str) -> dict:
    """Flatten (setup, DecisionTrace) → a JSONL-serializable row.

    `now` must be tz-aware US/Eastern. `source` is "live" or "backfill".
    """
    return {
        "ts":          now.isoformat(),
        "date":        now.date().isoformat(),
        "source":      source,
        "strategy":    setup.strategy,
        "conviction":  setup.conviction,
        "score":       setup.score,
        "direction":   setup.direction,
        "trend":       setup.trend,
        "passed_tier": trace["passed_tier"],
        "accepted":    [a["dte_bucket"] for a in trace["accepted"]],
        "rejected":    trace["rejected"],
    }


def _track_dir() -> str:
    return os.path.join(config.LOG_DIR, "learning", "router_track")


def write_trace(record: dict, day=None) -> str:
    """Append `record` as one JSON line to
    logs/learning/router_track/<day>.jsonl. `day` defaults to record["date"].
    Returns the file path written.
    """
    day_s = (day.isoformat() if hasattr(day, "isoformat") else day) or record["date"]
    path = os.path.join(_track_dir(), f"{day_s}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    return path
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_router_tracker.py -v`
Expected: PASS (3 tests).

- [x] **Step 5: Commit**

```bash
git add learning/router_tracker.py tests/test_router_tracker.py
git commit -m "feat: add router_tracker (flatten + append JSONL trace rows)"
```

---

## Task 5: `backtests/router_track_backfill.py` — replay 2024

**Files:**
- Create: `backtests/router_track_backfill.py`
- Test: `tests/test_router_track_backfill.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_router_track_backfill.py`:

```python
import json
import os
import sys
from datetime import date

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from signals.spy_options_engine import SPYSetup

ET = pytz.timezone("US/Eastern")


def _setup(strategy="iron_condor", conviction="high", score=70):
    return SPYSetup(strategy=strategy, conviction=conviction, timeframe="intraday",
                    score=score, reasons=["r"], direction="neutral", trend="range-bound")


def _read_all(track_dir):
    rows = []
    for fn in sorted(os.listdir(track_dir)):
        with open(os.path.join(track_dir, fn)) as f:
            rows += [json.loads(ln) for ln in f if ln.strip()]
    return rows


def test_backfill_two_weekdays_writes_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    # Mon 2024-07-15 and Tue 2024-07-16, one setup each.
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup()])
    n = bf.backfill(date(2024, 7, 15), date(2024, 7, 16))
    assert n == 2
    rows = _read_all(os.path.join(str(tmp_path), "learning", "router_track"))
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"backfill"}


def test_backfill_skips_weekend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup()])
    # 2024-07-13 Sat, 2024-07-14 Sun → zero rows.
    n = bf.backfill(date(2024, 7, 13), date(2024, 7, 14))
    assert n == 0


def test_backfill_fresh_broker_per_day_no_cross_day_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    # Same single iron_condor setup each day; morning eval → 0DTE accepted.
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup()])
    bf.backfill(date(2024, 7, 15), date(2024, 7, 16))
    rows = _read_all(os.path.join(str(tmp_path), "learning", "router_track"))
    # Day 2 must still ACCEPT (fresh broker) — not be dedup-blocked by day 1.
    assert all(r["accepted"] for r in rows)


def test_backfill_accepted_recorded_forward_same_day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    # Two identical iron_condor setups SAME day → 2nd hits dedup (open in combo).
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup(), _setup()])
    bf.backfill(date(2024, 7, 15), date(2024, 7, 15))
    rows = _read_all(os.path.join(str(tmp_path), "learning", "router_track"))
    assert len(rows) == 2
    assert rows[0]["accepted"] == ["0DTE"]
    assert rows[1]["accepted"] == []          # blocked by the first's open position
    assert any(x["gate"] == "dedup" for x in rows[1]["rejected"])


def test_backfill_empty_setups_day_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [])
    n = bf.backfill(date(2024, 7, 15), date(2024, 7, 15))
    assert n == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_router_track_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtests.router_track_backfill'`.

- [x] **Step 3: Write minimal implementation**

Create `backtests/router_track_backfill.py`:

```python
"""backtests/router_track_backfill.py -- Offline replay of cached history
through route_explain(), writing trace rows (source='backfill').

Mirrors the walk-forward's model: one evaluation per trading day at 09:45 ET,
a fresh _MockBroker per day (no cross-day dedup leak), accepted buckets
recorded forward within the day so same-day same-combo setups hit dedup.

CLI:
    python -m backtests.router_track_backfill --start 2024-01-02 --end 2024-12-31
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytz
from loguru import logger

from backtests.router_setup_builder import build_historical_setup
from backtests.intraday_router_wf import _MockBroker
from signals.intraday_entry_router import route_explain
from learning.router_tracker import build_trace_record, write_trace

_ET = pytz.timezone("US/Eastern")


def backfill(start: date, end: date) -> int:
    """Replay [start, end] (inclusive) through route_explain; append trace rows
    to logs/learning/router_track/<day>.jsonl. Returns rows written."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:                              # Standing Rule #1: weekdays only
            try:
                setups = build_historical_setup(d)
            except ValueError as e:                      # insufficient history at range start
                logger.debug(f"backfill: skip {d}: {e}")
                setups = []
            broker = _MockBroker()                       # fresh per day
            ts = _ET.localize(datetime.combine(d, time(9, 45)))
            for setup in setups:
                trace = route_explain(setup, ts, broker)
                write_trace(build_trace_record(setup, trace, ts, "backfill"), day=d)
                n += 1
                for a in trace["accepted"]:              # record opens forward within the day
                    broker.record_open(strategy=setup.strategy, dte_bucket=a["dte_bucket"])
        d += timedelta(days=1)
    logger.info(f"backfill: {n} trace rows over {start}..{end}")
    return n


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill router decision traces")
    parser.add_argument("--start", default="2024-01-02", help="ISO date")
    parser.add_argument("--end",   default="2024-12-31", help="ISO date")
    args = parser.parse_args()
    total = backfill(date.fromisoformat(args.start), date.fromisoformat(args.end))
    logger.info(f"backfill: done, {total} rows")
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_router_track_backfill.py -v`
Expected: PASS (5 tests).

- [x] **Step 5: Commit**

```bash
git add backtests/router_track_backfill.py tests/test_router_track_backfill.py
git commit -m "feat: add router_track_backfill (replay cached history via route_explain)"
```

---

## Task 6: `backtests/router_track_rollup.py` — JSONL → parquet

**Files:**
- Create: `backtests/router_track_rollup.py`
- Test: `tests/test_router_track_rollup.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_router_track_rollup.py`:

```python
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.router_track_rollup import load_jsonl_dir, rollup_to_parquet


def _row(accepted, rejected_gates):
    return {
        "ts": "2024-07-15T09:45:00-04:00", "date": "2024-07-15", "source": "backfill",
        "strategy": "iron_condor", "conviction": "high", "score": 70,
        "direction": "neutral", "trend": "range-bound", "passed_tier": True,
        "accepted": accepted,
        "rejected": [{"dte_bucket": "x", "gate": g, "detail": "d"} for g in rejected_gates],
    }


def test_load_jsonl_dir_reads_multiple_files_and_skips_corrupt(tmp_path, caplog):
    import json
    (tmp_path / "a.jsonl").write_text(json.dumps(_row(["0DTE"], [])) + "\n")
    (tmp_path / "b.jsonl").write_text(
        json.dumps(_row([], ["dte"])) + "\n" + "{not valid json\n")
    records = load_jsonl_dir(str(tmp_path))
    assert len(records) == 2          # 2 valid rows; the corrupt line skipped


def test_rollup_to_parquet_columns_and_flattening(tmp_path):
    records = [_row(["0DTE"], ["dte", "dedup"]), _row([], [])]
    out = str(tmp_path / "rollup.parquet")
    rollup_to_parquet(records, out)
    df = pd.read_parquet(out)
    assert list(df.columns) == [
        "ts", "date", "source", "strategy", "conviction", "score",
        "direction", "trend", "passed_tier", "n_accepted", "n_rejected", "reject_gates"]
    assert df.iloc[0]["n_accepted"] == 1
    assert df.iloc[0]["n_rejected"] == 2
    assert df.iloc[0]["reject_gates"] == "dedup,dte"     # sorted, distinct, comma-joined
    assert df.iloc[1]["reject_gates"] == ""


def test_rollup_empty_dir_writes_schema_only_parquet(tmp_path):
    records = load_jsonl_dir(str(tmp_path))             # empty dir → []
    out = str(tmp_path / "empty.parquet")
    rollup_to_parquet(records, out)
    df = pd.read_parquet(out)
    assert len(df) == 0
    assert "reject_gates" in df.columns
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_router_track_rollup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtests.router_track_rollup'`.

- [x] **Step 3: Write minimal implementation**

Create `backtests/router_track_rollup.py`:

```python
"""backtests/router_track_rollup.py -- Compact router_track JSONL → one parquet.

Reads every logs/learning/router_track/*.jsonl row and writes a columnar
parquet (counts + distinct reject gates) for the calibration / analysis
tooling. The JSONL remains the lossless source of truth.

CLI:
    python -m backtests.router_track_rollup
"""

from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config

_COLUMNS = ["ts", "date", "source", "strategy", "conviction", "score",
            "direction", "trend", "passed_tier", "n_accepted", "n_rejected",
            "reject_gates"]

_DEFAULT_OUT = os.path.join(os.path.dirname(__file__), ".cache", "router_track",
                            "router_track.parquet")


def load_jsonl_dir(dir_path: str) -> list[dict]:
    """Read every *.jsonl row in dir_path. Corrupt lines are skipped with a
    warning (never aborts the rollup). Missing dir → []."""
    records: list[dict] = []
    for path in sorted(glob.glob(os.path.join(dir_path, "*.jsonl"))):
        with open(path) as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"rollup: skip corrupt line {path}:{i}: {e}")
    return records


def rollup_to_parquet(records: list[dict], out_path: str) -> str:
    """Flatten records → parquet at out_path. Returns out_path."""
    rows = [{
        "ts": r["ts"], "date": r["date"], "source": r["source"],
        "strategy": r["strategy"], "conviction": r["conviction"],
        "score": r["score"], "direction": r.get("direction"),
        "trend": r.get("trend"), "passed_tier": r["passed_tier"],
        "n_accepted": len(r["accepted"]),
        "n_rejected": len(r["rejected"]),
        "reject_gates": ",".join(sorted({x["gate"] for x in r["rejected"]})),
    } for r in records]
    df = pd.DataFrame(rows, columns=_COLUMNS)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_parquet(out_path)
    logger.info(f"rollup: {len(df)} rows → {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Roll up router_track JSONL → parquet")
    parser.add_argument("--in",  dest="in_dir",
                        default=os.path.join(config.LOG_DIR, "learning", "router_track"))
    parser.add_argument("--out", default=_DEFAULT_OUT)
    args = parser.parse_args()
    rollup_to_parquet(load_jsonl_dir(args.in_dir), args.out)
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_router_track_rollup.py -v`
Expected: PASS (3 tests).

- [x] **Step 5: Commit**

```bash
git add backtests/router_track_rollup.py tests/test_router_track_rollup.py
git commit -m "feat: add router_track_rollup (JSONL -> parquet for calibration)"
```

---

## Task 7: End-to-end integration test (real cached data)

**Files:**
- Test: `tests/test_router_track_integration.py`

- [x] **Step 1: Write the integration test**

Create `tests/test_router_track_integration.py`:

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


@pytest.mark.integration
def test_backfill_then_rollup_on_real_april_2024(tmp_path, monkeypatch):
    """Real cached SPY 5-min parquet (backtests/.cache) → backfill → rollup."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    from datetime import date
    from backtests.router_track_backfill import backfill
    from backtests.router_track_rollup import load_jsonl_dir, rollup_to_parquet
    import pandas as pd

    n = backfill(date(2024, 4, 1), date(2024, 4, 5))
    assert n >= 1                                   # April 2024 has cached data + qualifying setups
    track_dir = os.path.join(str(tmp_path), "learning", "router_track")
    out = str(tmp_path / "rollup.parquet")
    rollup_to_parquet(load_jsonl_dir(track_dir), out)
    df = pd.read_parquet(out)
    assert len(df) == n
    assert df["source"].eq("backfill").all()
```

- [x] **Step 2: Run the integration test**

Run: `pytest tests/test_router_track_integration.py -v -m integration`
Expected: PASS. If it fails with empty data, confirm `backtests/.cache/SPY_5minute_2024-04-*.parquet` exist (they do per the spec's data audit) and that `build_historical_setup` finds ≥30 daily bars before 2024-04-01 in `backtests/spy_history.csv`.

- [x] **Step 3: Commit**

```bash
git add tests/test_router_track_integration.py
git commit -m "test: end-to-end backfill+rollup integration on real April 2024 data"
```

---

## Task 8: Seed the real 2024 backfill + rollup (one-shot data generation)

Not a code change — produces the artifact calibration consumes. Run after Tasks 1-7 are green.

- [x] **Step 1: Run the full-year backfill**

Run: `python -m backtests.router_track_backfill --start 2024-01-02 --end 2024-12-31`
Expected: a `backfill: N trace rows over 2024-01-02..2024-12-31` log line with N ≥ 100. First run may hit Polygon for any day not already cached; cached days are free.

- [x] **Step 2: Roll up to parquet**

Run: `python -m backtests.router_track_rollup`
Expected: `rollup: N rows → .../backtests/.cache/router_track/router_track.parquet`.

- [x] **Step 3: Sanity-check the artifact**

Run:
```bash
python -c "import pandas as pd; df=pd.read_parquet('backtests/.cache/router_track/router_track.parquet'); print(len(df), 'rows'); print(df['reject_gates'].value_counts()); print(df['n_accepted'].sum(),'accepted buckets')"
```
Expected: non-zero rows; a distribution of `reject_gates` (tier/dte/dedup combos) — this is the gating-behavior data calibration will reference.

- [x] **Step 4: Note the artifact location for the next session**

The parquet under `backtests/.cache/router_track/` is git-ignored (it's under `.cache/`). Record in BUILD_LOG.md that the backfill artifact exists and how to regenerate it (the two commands above). No commit of the parquet itself.

---

## Final verification (before declaring Phase 1 done)

- [x] **Run the full non-integration suite**

Run: `pytest tests/ -v -m "not integration" --tb=short`
Expected: all green, including the 4 new test files. (Per Standing Rule #5, this gate must pass before any push.)

- [x] **Confirm `route()` behavior is unchanged**

Run: `pytest tests/test_intraday_entry_router.py tests/test_intraday_router_wf.py -v`
Expected: all pre-existing router tests still pass (the Task 1 refactor was behavior-preserving).

- [x] **Append a BUILD_LOG.md entry** summarizing Phase 1 (route_explain + tracker + backfill/rollup, the seeded 2024 artifact, and that Phase 2 live wiring remains deferred).

---

## Self-Review (completed by plan author)

**Spec coverage:**
- `route_explain()` + DRY dedup refactor → Tasks 1-3 ✓
- Trace record schema / `build_trace_record` / `write_trace` (JSONL) → Task 4 ✓
- Backfill (per-day fresh broker, 09:45 ET, record-forward, weekday-only) → Task 5 ✓
- Rollup (load_jsonl_dir skip-corrupt, parquet columns incl. `reject_gates`) → Task 6 ✓
- Integration test on real cached data → Task 7 ✓
- Seeding the real artifact for calibration → Task 8 ✓
- Phase 2 (live scanner seam) → explicitly out of scope (deferred) ✓
- Error-handling rows (corrupt line, empty dir, insufficient history, no cross-day leak) → covered by Task 5/6 tests ✓
- Regime omission / `trend` proxy → reflected in Task 4 record shape ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; every command shows expected output. ✓

**Type/name consistency:** `route_explain` returns `{passed_tier, candidate_buckets, accepted: list[{dte_bucket}], rejected: list[{dte_bucket, gate, detail}]}` consistently across Tasks 3-6; `build_trace_record(setup, trace, now, source)` and `write_trace(record, day=None)` signatures match between Task 4 definition and Task 5 use; `_dedup_partition` return `(allowed, [(bucket, reason)])` matches its use in Task 3; parquet `_COLUMNS` in Task 6 matches the test's asserted column list. ✓
