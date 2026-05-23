# Phase 2a — Per-Strategy Tags + Walk-Forward Primitive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the data substrate Phase 2b/3 will sit on — extend `Prediction` records, trade records, and the exit log with `strategy` / `dte_bucket` / `book` tags so per-sub-strategy edge measurement becomes possible. Also extract the common walk-forward 60/40 split + metrics primitive into a single module so future per-sub-strategy harnesses don't re-implement it.

**Architecture:** Three tasks, all additive/refactor. **No live trading behavior changes.** All new fields default to None or to back-compat values (paper-broker hardcodes `dte_bucket="45DTE"`, `book="disciplined"` since that's all it currently produces; Phase 3's intraday plumbing will populate the others). The shared walk-forward primitive is created but existing harnesses are NOT migrated to it (future migration is lazy/opt-in).

**Tech Stack:** Python, pandas, numpy. Reuses `journal/trade_recorder.py`, `learning/predictions.py`, `learning/paper_broker.py`, `backtests/` harnesses.

**Spec:** Derived from the strategic to-do list items #4, #14, #28. Design decisions locked in the 2026-05-23 strategic conversation.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `learning/predictions.py` | Modify | Add `strategy` / `dte_bucket` / `book` optional fields to `Prediction` dataclass (default None) |
| `journal/trade_recorder.py` | Modify | Add `dte_bucket` and `book` kwargs to `log_entry`; add optional `exit_reason` kwarg to `log_exit`; add `get_trades_by(...)` filter helper |
| `learning/paper_broker.py` | Modify | Pass `dte_bucket="45DTE"`, `book="disciplined"` when creating Predictions + logging trade entries |
| `backtests/wf_common.py` | Create | Shared `split_oos` + `metrics_block` + `OOS_FRACTION` primitive |
| Tests under `tests/` | Various | Per task |

---

## Task 1: Per-strategy logging tags on Prediction + TradeRecorder + paper_broker

**Files:**
- Modify: `learning/predictions.py` (`Prediction` dataclass, lines 52-71)
- Modify: `journal/trade_recorder.py` (`log_entry` signature, lines 46-90)
- Modify: `learning/paper_broker.py` (`execute()` method, populates tags when creating Prediction + calling log_entry)
- Test: `tests/test_per_strategy_tags.py`

The tags are additive. All new fields default to `None`. Old JSON/JSONL entries deserialize unchanged. Old callers of `log_entry()` keep working — the new kwargs are optional with `None` defaults. Phase 3+ will populate them; Phase 2a wires the substrate.

### Step 1: Write the failing tests — `tests/test_per_strategy_tags.py`:

```python
"""Phase 2a: Prediction + TradeRecorder + paper_broker carry strategy /
dte_bucket / book tags so per-sub-strategy edge measurement is possible.

All new fields default to None; old records deserialize unchanged."""

import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from learning.predictions import Prediction, PredictionLog
from journal.trade_recorder import TradeRecorder


# ── Prediction dataclass ──────────────────────────────────────────────────

def test_prediction_new_fields_default_to_none():
    p = Prediction(date="2026-05-26", regime="trending_up_calm",
                   direction="bullish", tradeable=True)
    assert p.strategy   is None
    assert p.dte_bucket is None
    assert p.book       is None


def test_prediction_new_fields_accept_strings():
    p = Prediction(date="2026-05-26", regime="trending_up_calm",
                   direction="bullish", tradeable=True,
                   strategy="iron_condor", dte_bucket="0DTE", book="learning")
    assert p.strategy   == "iron_condor"
    assert p.dte_bucket == "0DTE"
    assert p.book       == "learning"


def test_prediction_roundtrip_through_jsonl(tmp_path, monkeypatch):
    """Old (untagged) prediction entries must still deserialize."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    log = PredictionLog()
    log.save(Prediction(date="2026-05-26", regime="trending_up_calm",
                        direction="bullish", tradeable=True,
                        strategy="bull_debit", dte_bucket="45DTE",
                        book="disciplined"))
    log.save(Prediction(date="2026-05-27", regime="choppy_low_vol",
                        direction="neutral", tradeable=True))   # untagged
    rows = log.all()
    assert len(rows) == 2
    tagged = next(r for r in rows if r["date"] == "2026-05-26")
    untagged = next(r for r in rows if r["date"] == "2026-05-27")
    assert tagged["strategy"]    == "bull_debit"
    assert tagged["dte_bucket"]  == "45DTE"
    assert tagged["book"]        == "disciplined"
    assert untagged.get("strategy")   is None
    assert untagged.get("dte_bucket") is None
    assert untagged.get("book")       is None


# ── TradeRecorder.log_entry ───────────────────────────────────────────────

def test_log_entry_accepts_dte_bucket_and_book_kwargs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(
        ticker="SPY", entry_price=2.50, size=1,
        trade_type="option_spread", strategy="iron_condor",
        direction="neutral", mode="swing",
        legs=[], max_profit=250.0, max_loss=250.0,
        dte_bucket="0DTE", book="learning",
    )
    assert tid
    trade = rec.get_trade_by_id(tid)
    assert trade["dte_bucket"] == "0DTE"
    assert trade["book"]       == "learning"


def test_log_entry_defaults_new_fields_to_none(tmp_path, monkeypatch):
    """Existing callers that don't pass dte_bucket/book still work."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(
        ticker="SPY", entry_price=1.10, size=1,
        trade_type="option_spread", strategy="debit_spread",
        direction="bullish", mode="swing", legs=[],
    )
    trade = rec.get_trade_by_id(tid)
    assert trade.get("dte_bucket") is None
    assert trade.get("book")       is None
```

### Step 2: Run, verify FAILS.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_per_strategy_tags.py -v`
Expected: 5 failures — `Prediction` lacks new fields; `log_entry` rejects unknown kwargs.

### Step 3: Extend `Prediction` dataclass in `learning/predictions.py`. Find the dataclass block (lines 52-71). After the existing `outcome: str | None = None` field (and inside the `@dataclass class Prediction:` block), insert these three lines BEFORE the closing line of the dataclass (i.e. as additional fields):

```python
    # Per-strategy tags (Phase 2a) — populated by paper_broker, read by
    # downstream Phase 3+ analytics.
    strategy:         str | None    = None   # e.g. "iron_condor", "bull_debit", "put_debit_spread"
    dte_bucket:       str | None    = None   # "0DTE" / "1-3DTE" / "45DTE"
    book:             str | None    = None   # "disciplined" / "learning"
```

Insert them immediately after the `outcome: str | None = None` line (the last existing field). The `@dataclass` decorator handles serialization automatically.

### Step 4: Extend `TradeRecorder.log_entry` in `journal/trade_recorder.py`. Find the signature (lines 46-60). Add two new optional kwargs to the SIGNATURE, immediately after the existing `notes: str = ""` line — but BEFORE the closing `) -> str:` — making the new signature:

```python
    def log_entry(
        self,
        ticker:          str,
        entry_price:     float,       # For stocks: share price. For spreads: net debit/credit
        size:            float,       # Shares for stock, contracts for options
        trade_type:      str  = "stock",
        strategy:        str  = None, # debit_spread, credit_spread, iron_condor, single_leg
        direction:       str  = "bullish",
        mode:            str  = "swing",
        legs:            list = None, # List of leg dicts for options spreads
        max_profit:      float = None,
        max_loss:        float = None,
        alert_timestamp: str  = None,
        alert_score:     int  = None,
        notes:           str  = "",
        dte_bucket:      str | None = None,   # "0DTE" / "1-3DTE" / "45DTE"
        book:            str | None = None,   # "disciplined" / "learning"
    ) -> str:
```

Then find where the trade dict is built inside `log_entry` (it stores fields like `"ticker"`, `"strategy"`, `"direction"`, etc., before persisting). Add these two fields to that dict alongside the existing ones:

```python
            "dte_bucket": dte_bucket,
            "book":       book,
```

(Read the function body around lines 90-159 to find the exact spot — there is a `trade = { ... }` literal that assembles the record before it's appended.)

### Step 5: Run, verify all 5 PASS.

### Step 6: Wire paper_broker to populate the tags. The bot currently produces only the 45DTE swing play, so for Phase 2a the broker hardcodes `dte_bucket="45DTE"`, `book="disciplined"`. Phase 3 will populate intraday brokers with their actual values.

Find `learning/paper_broker.py`, the `execute()` method. It calls (a) `PredictionLog.save(Prediction(...))` and (b) `TradeRecorder.log_entry(...)`. Add the tags to BOTH call sites:

(a) In the `Prediction(...)` constructor call inside `execute()`, add:
```python
            strategy        = ...,   # use play["options"]["strategy"] if present, else None
            dte_bucket      = "45DTE",
            book            = "disciplined",
```
(The `strategy` value comes from the play dict — read it as `play.get("options", {}).get("strategy")` so a skip day with no options payload returns None safely.)

(b) In the `log_entry(...)` call inside `execute()`, add:
```python
            dte_bucket = "45DTE",
            book       = "disciplined",
```

Read `paper_broker.py` to find the exact lines — the `execute()` method has both calls (Prediction save first, then conditional log_entry for tradeable). Both need the same hardcoded tags for Phase 2a.

### Step 7: Run focused tests, verify they still PASS, plus add a smoke test for paper_broker:

Append this test to `tests/test_per_strategy_tags.py`:

```python
# ── paper_broker integration ──────────────────────────────────────────────

def test_paper_broker_populates_45dte_disciplined_tags(tmp_path, monkeypatch):
    """paper_broker, when it writes a Prediction + trade entry, must populate
    dte_bucket='45DTE' and book='disciplined' since that's all the bot currently
    produces. Phase 3's intraday brokers will populate other values."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")

    from learning.paper_broker import PaperBroker
    broker = PaperBroker()
    play = {
        "date":       "2026-05-26",
        "tradeable":  True,
        "regime":     "trending_up_calm",
        "confidence": 0.8,
        "reasons":    ["trend intact"],
        "metrics":    {"spy_close": 740.0, "ma200": 678.0, "ma200_dist_%": 9.0,
                       "adx": 34.0, "vix": 17.0, "ivr": 40.0},
        "options": {
            "tradeable":     True,
            "strategy":      "debit_spread",
            "direction":     "bullish",
            "entry_price":   1.10,
            "max_profit":    200.0,
            "max_loss":      110.0,
            "legs":          [],
        },
    }
    broker.execute(play)

    # Verify the Prediction record was tagged.
    from learning.predictions import PredictionLog
    pred = PredictionLog().get("2026-05-26")
    assert pred is not None
    assert pred["dte_bucket"] == "45DTE"
    assert pred["book"]       == "disciplined"

    # Verify the trade record was tagged.
    from journal.trade_recorder import TradeRecorder
    trades = TradeRecorder().get_all_trades()
    assert len(trades) >= 1
    t = trades[-1]
    assert t["dte_bucket"] == "45DTE"
    assert t["book"]       == "disciplined"
```

Re-run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_per_strategy_tags.py -v`
Expected: 6 passed (5 original + 1 new paper_broker smoke).

### Step 8: Run the FULL non-integration suite to confirm no regressions:
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: all pass (708 baseline + new). If a pre-existing test asserts on exact Prediction or trade-dict shape and breaks because of the new fields, READ the test and update it — the new fields default to None and should be backward-compatible.

### Step 9: Commit:
```bash
git add learning/predictions.py journal/trade_recorder.py learning/paper_broker.py tests/test_per_strategy_tags.py
git commit -m "feat: strategy/dte_bucket/book tags on Prediction + TradeRecorder + paper_broker

Per-sub-strategy edge measurement substrate for Phases 2b/3. Tags are
additive — all new fields default to None; old JSONL/JSON entries
deserialize unchanged; existing log_entry callers that don't pass the
new kwargs keep working. paper_broker (currently the only producer)
hardcodes dte_bucket='45DTE', book='disciplined' since that's all the
bot produces today; Phase 3's intraday brokers will populate other values."
```

---

## Task 2: Per-sub-strategy exit-reason tracking + query helper

**Files:**
- Modify: `journal/trade_recorder.py` (`log_exit` adds optional `exit_reason: str | None = None` kwarg; persisted as structured field; new `get_trades_by(...)` filter helper)
- Test: `tests/test_exit_reason_tracking.py`

The existing `log_exit(trade_id, exit_price, notes="")` just stores the exit reason as text inside `notes`. For per-sub-strategy aggregation we need it as a structured field. Builds on Task 1's tags so query helpers can filter.

### Step 1: Write the failing test — `tests/test_exit_reason_tracking.py`:

```python
"""Phase 2a: TradeRecorder.log_exit accepts an optional structured exit_reason
field. TradeRecorder.get_trades_by(...) filters by the Task 1 tags."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.trade_recorder import TradeRecorder


def _seed(rec):
    """Open + close three trades across different sub-strategies."""
    t1 = rec.log_entry(ticker="SPY", entry_price=1.10, size=1,
                       trade_type="option_spread", strategy="debit_spread",
                       direction="bullish", mode="swing", legs=[],
                       dte_bucket="45DTE", book="disciplined")
    t2 = rec.log_entry(ticker="SPY", entry_price=2.50, size=1,
                       trade_type="option_spread", strategy="iron_condor",
                       direction="neutral", mode="swing", legs=[],
                       dte_bucket="0DTE", book="learning")
    t3 = rec.log_entry(ticker="SPY", entry_price=0.80, size=1,
                       trade_type="option_spread", strategy="debit_spread",
                       direction="bullish", mode="swing", legs=[],
                       dte_bucket="45DTE", book="disciplined")
    rec.log_exit(t1, exit_price=2.00, exit_reason="target")
    rec.log_exit(t2, exit_price=2.00, exit_reason="stop")
    # t3 left open
    return t1, t2, t3


def test_log_exit_accepts_and_persists_exit_reason(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    t1, t2, _ = _seed(rec)
    closed1 = rec.get_trade_by_id(t1)
    closed2 = rec.get_trade_by_id(t2)
    assert closed1["exit_reason"] == "target"
    assert closed2["exit_reason"] == "stop"


def test_log_exit_without_reason_leaves_field_none(tmp_path, monkeypatch):
    """Existing log_exit callers that don't pass exit_reason still work."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    tid = rec.log_entry(ticker="SPY", entry_price=1.0, size=1,
                        trade_type="option_spread", strategy="iron_condor",
                        direction="neutral", mode="swing", legs=[])
    rec.log_exit(tid, exit_price=0.50)   # no exit_reason
    t = rec.get_trade_by_id(tid)
    assert t.get("exit_reason") is None


def test_get_trades_by_filters_strategy(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    condors = rec.get_trades_by(strategy="iron_condor")
    assert len(condors) == 1
    assert condors[0]["strategy"] == "iron_condor"
    debits = rec.get_trades_by(strategy="debit_spread")
    assert len(debits) == 2


def test_get_trades_by_filters_book_and_dte_bucket(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    learning = rec.get_trades_by(book="learning")
    assert [t["strategy"] for t in learning] == ["iron_condor"]
    dte0 = rec.get_trades_by(dte_bucket="0DTE")
    assert len(dte0) == 1
    combined = rec.get_trades_by(strategy="debit_spread",
                                  dte_bucket="45DTE", book="disciplined")
    assert len(combined) == 2


def test_get_trades_by_no_filter_returns_all(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    assert len(rec.get_trades_by()) == 3


def test_get_trades_by_filters_exit_reason(tmp_path, monkeypatch):
    """Filtering by exit_reason — useful for 'which trades hit target?'"""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    rec = TradeRecorder()
    _seed(rec)
    targets = rec.get_trades_by(exit_reason="target")
    assert len(targets) == 1
    stops = rec.get_trades_by(exit_reason="stop")
    assert len(stops) == 1
```

### Step 2: Run, verify FAILS.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_exit_reason_tracking.py -v`
Expected: 6 failures — `log_exit` rejects `exit_reason` kwarg; `get_trades_by` doesn't exist.

### Step 3: Extend `TradeRecorder.log_exit` in `journal/trade_recorder.py`.

Find the existing signature (lines 159-180):
```python
    def log_exit(
        self,
        trade_id:   str,
        exit_price: float,    # ...
        notes:      str = "",
    ) -> bool:
```
Add an optional `exit_reason` kwarg AFTER `notes`:
```python
    def log_exit(
        self,
        trade_id:   str,
        exit_price: float,    # For spreads: net credit received to close (debit spread)
                              #              or net debit paid to close (credit spread)
        notes:      str = "",
        exit_reason: str | None = None,   # "target" / "stop" / "time_stop" / "target_intraday" / "expiry"
    ) -> bool:
```

Then inside the function body, find where the trade dict is updated on exit (the `for trade in trades` loop that mutates the trade record). After the existing exit-related field assignments (`trade["exit_price"]`, `trade["exit_pnl_dollars"]`, `trade["notes_exit"]`, etc.), add:
```python
            trade["exit_reason"] = exit_reason
```

### Step 4: Add `get_trades_by` method to `TradeRecorder`. After the existing query helpers (`get_all_trades`, `get_open_trades`, `get_closed_trades`, `get_trade_by_id`, `get_trades_for_ticker`, `get_summary_stats`), add:

```python
    def get_trades_by(self, *, strategy: str | None = None,
                      dte_bucket: str | None = None,
                      book: str | None = None,
                      exit_reason: str | None = None) -> list:
        """Filter trades by optional tag values. Trades that lack a tag are
        EXCLUDED from filters that specify that tag — old (untagged) trades
        don't participate in strategy/book/dte_bucket searches.

        No-filter call returns all trades.
        """
        rows = self.get_all_trades()
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

### Step 5: Run focused tests, verify all 6 PASS.

### Step 6: Run FULL non-integration suite to confirm no regressions.
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/ -m "not integration" -q 2>&1 | tail -3`
Expected: all pass.

### Step 7: Commit:
```bash
git add journal/trade_recorder.py tests/test_exit_reason_tracking.py
git commit -m "feat: structured exit_reason on log_exit + get_trades_by() filter API

Per-sub-strategy exit-reason aggregation substrate. log_exit now accepts
an optional structured exit_reason (target / stop / time_stop /
target_intraday / expiry); old callers that pass only notes keep working
unchanged. get_trades_by() composes filters across strategy / dte_bucket
/ book / exit_reason for Phase 3+ analytics."
```

---

## Task 3: `backtests/wf_common.py` — shared walk-forward primitive

**Files:**
- Create: `backtests/wf_common.py`
- Test: `tests/test_wf_common.py`

Extracts the common chronological 60/40 split + per-slice metrics into one module. Future per-sub-strategy harnesses opt into it. **Do NOT refactor existing harnesses** (`walk_forward.py`, `condor_in_trend_wf.py`, `intraday_touch_wf.py`, `meta_trainer`, `hypothesis_runner`) — they all work; lazy migration is fine.

### Step 1: Write the failing test — `tests/test_wf_common.py`:

```python
"""Phase 2a: shared walk-forward primitive — chronological 60/40 split +
per-slice metrics. Future harnesses opt in; existing ones unchanged."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from backtests.wf_common import (
    OOS_FRACTION_DEFAULT, IS_FRACTION_DEFAULT,
    split_oos, metrics_block,
)


def _frame(n=200):
    """A backtest-results-shaped frame: date, tradeable, outcome, pnl."""
    d0 = pd.Timestamp("2022-01-01")
    return pd.DataFrame({
        "date":      [d0 + pd.Timedelta(days=i) for i in range(n)],
        "tradeable": [True] * n,
        "outcome":   (["win"] * 120) + (["loss"] * 60) + (["breakeven"] * 20),
        "pnl":       ([120] * 120) + ([-100] * 60) + ([0] * 20),
    })


def test_default_fractions_are_60_40():
    """The codebase convention: IS = first 60% of dates, OOS = last 40%."""
    assert IS_FRACTION_DEFAULT  == 0.60
    assert OOS_FRACTION_DEFAULT == 0.40
    # And they sum to 1 (no gap, no overlap).
    assert IS_FRACTION_DEFAULT + OOS_FRACTION_DEFAULT == 1.0


def test_split_oos_returns_chronological_split():
    df = _frame(n=200)
    ins, oos = split_oos(df)
    assert len(ins) == 120          # 60% of 200
    assert len(oos) == 80           # 40% of 200
    # Chronological: every IS date is before every OOS date.
    assert ins["date"].max() < oos["date"].min()


def test_split_oos_respects_custom_in_sample_fraction():
    df = _frame(n=100)
    ins, oos = split_oos(df, in_sample_fraction=0.70)
    assert len(ins) == 70
    assert len(oos) == 30


def test_split_oos_uses_custom_date_column():
    df = pd.DataFrame({
        "entry_date": pd.date_range("2025-01-01", periods=10),
        "tradeable":  [True] * 10,
        "outcome":    ["win"] * 10,
        "pnl":        [100] * 10,
    })
    ins, oos = split_oos(df, date_col="entry_date")
    assert len(ins) == 6 and len(oos) == 4


def test_metrics_block_computes_trades_winrate_pnl_sharpe():
    df = _frame(n=200)
    m = metrics_block(df)
    assert m["trades"]   == 200       # 120 wins + 60 losses + 20 breakevens
    assert m["win_rate"] == 60.0      # 120 / 200
    assert m["pnl"]      == 120 * 120 + 60 * -100 + 20 * 0      # 14400 - 6000 = 8400
    assert m["sharpe"]   > 0.0        # rising avg pnl → positive sharpe


def test_metrics_block_handles_empty_slice():
    df = pd.DataFrame({"date": [], "tradeable": [], "outcome": [], "pnl": []})
    m = metrics_block(df)
    assert m == {"trades": 0, "win_rate": 0.0, "pnl": 0, "sharpe": 0.0}


def test_metrics_block_ignores_non_tradeable_rows():
    """skip days (tradeable=False) shouldn't count in the metrics."""
    df = pd.DataFrame({
        "date":      pd.date_range("2025-01-01", periods=10),
        "tradeable": [True, True, False, False, True, True, False, True, True, True],
        "outcome":   ["win"]*2 + ["skip"]*2 + ["loss"]*2 + ["skip"] + ["win"]*3,
        "pnl":       [100, 100, 0, 0, -50, -50, 0, 100, 100, 100],
    })
    m = metrics_block(df)
    assert m["trades"] == 7                                    # 5 wins + 2 losses
    assert m["win_rate"] == round(5 / 7 * 100, 1)
    assert m["pnl"] == 100*5 + (-50)*2                          # 500 - 100 = 400


def test_split_then_metrics_workflow():
    """End-to-end: split data 60/40, compute metrics on each slice."""
    df = _frame(n=200)
    ins, oos = split_oos(df)
    m_is  = metrics_block(ins)
    m_oos = metrics_block(oos)
    # Both slices have non-zero stats (the synthetic frame has trades in both).
    assert m_is["trades"]  == 120
    assert m_oos["trades"] == 80
    # PnL aggregates to the whole frame's pnl
    assert m_is["pnl"] + m_oos["pnl"] == metrics_block(df)["pnl"]
```

### Step 2: Run, verify FAILS (ModuleNotFoundError on backtests.wf_common).
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_wf_common.py -v`

### Step 3: Create `backtests/wf_common.py`:

```python
"""
backtests/wf_common.py -- Shared walk-forward primitive for backtest harnesses.

Every walk-forward harness in this codebase does some variant of "split the
results chronologically by date, compute {trades, win_rate, pnl, sharpe} on
each slice, compare IS vs OOS." This module extracts that into one place so
future per-sub-strategy harnesses don't re-implement it (and gradually drift).

Convention used codebase-wide:
    IS  = first  IS_FRACTION_DEFAULT  (= 60%) of dates by chronological order
    OOS = last   OOS_FRACTION_DEFAULT (= 40%) of dates by chronological order

Existing harnesses (walk_forward.py, condor_in_trend_wf.py, intraday_touch_wf.py,
meta_trainer.passes_ship_bar, hypothesis_runner._default_backtest) currently
re-implement these primitives inline; they keep working as-is. New harnesses
should import from here; existing ones can migrate lazily.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

IS_FRACTION_DEFAULT  = 0.60
OOS_FRACTION_DEFAULT = 0.40


def split_oos(df: pd.DataFrame,
              in_sample_fraction: float = IS_FRACTION_DEFAULT,
              date_col: str = "date") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split: returns (in_sample_slice, oos_slice).

    The split is by ROW COUNT after sorting by date_col — the first
    `in_sample_fraction` of rows is in-sample, the rest is out-of-sample.
    Both slices preserve every column of the input frame.
    """
    out = df.copy()
    if date_col in out.columns:
        out[date_col] = pd.to_datetime(out[date_col])
        out = out.sort_values(date_col).reset_index(drop=True)
    cut = int(len(out) * in_sample_fraction)
    return out.iloc[:cut].copy(), out.iloc[cut:].copy()


def metrics_block(df: pd.DataFrame) -> dict:
    """Compute {trades, win_rate, pnl, sharpe} for a slice of backtest rows.

    Expects columns: tradeable (bool), outcome (str in {win,loss,breakeven,skip}),
    pnl (numeric). Rows with tradeable=False are excluded from all stats.
    Empty input returns the zero block (no division by zero).
    """
    if len(df) == 0:
        return {"trades": 0, "win_rate": 0.0, "pnl": 0, "sharpe": 0.0}

    traded = df[df["tradeable"] == True]
    closed = traded[traded["outcome"].isin(["win", "loss", "breakeven"])]
    n      = len(closed)
    wins   = len(closed[closed["outcome"] == "win"])
    wr     = round(wins / n * 100, 1) if n else 0.0
    pnl    = int(traded["pnl"].sum()) if len(traded) else 0
    daily  = traded["pnl"].values
    sharpe = (float((np.mean(daily) / (np.std(daily) + 1e-9)) * np.sqrt(252))
              if len(daily) > 1 else 0.0)
    return {
        "trades":   int(n),
        "win_rate": wr,
        "pnl":      pnl,
        "sharpe":   round(sharpe, 3),
    }
```

### Step 4: Run, verify all 8 PASS.

### Step 5: Confirm the module imports cleanly:
Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -c "from backtests.wf_common import split_oos, metrics_block, OOS_FRACTION_DEFAULT, IS_FRACTION_DEFAULT; print(IS_FRACTION_DEFAULT, OOS_FRACTION_DEFAULT)"`
Expected: `0.6 0.4`

### Step 6: Verify existing harnesses are UNCHANGED. Run a quick grep — there should be ZERO imports of `wf_common` outside `tests/`:
Run: `grep -rn "from backtests.wf_common\|import wf_common" backtests/ learning/ signals/ 2>/dev/null | grep -v tests`
Expected: empty output (no production-code import yet — lazy migration).

### Step 7: Commit:
```bash
git add backtests/wf_common.py tests/test_wf_common.py
git commit -m "feat: backtests/wf_common.py shared walk-forward primitive

Extracts the common chronological 60/40 split + per-slice metrics block
that every walk-forward harness in the codebase re-implements inline
(walk_forward.py, condor_in_trend_wf.py, intraday_touch_wf.py,
meta_trainer.passes_ship_bar, hypothesis_runner._default_backtest).

Existing harnesses are NOT refactored to use it — they keep working as-is;
new per-sub-strategy harnesses (Phase 2b+) opt in. Lazy migration of the
old ones prevents drift without forcing risky changes to working code."
```

---

## Self-Review Notes

**Spec coverage:**
- To-do #4 (per-strategy logging tags) → Task 1 ✓ (Prediction + TradeRecorder + paper_broker)
- To-do #14 (per-sub-strategy exit-reason tracking) → Task 2 ✓ (structured exit_reason + get_trades_by filter)
- To-do #28 (wf_common shared primitive) → Task 3 ✓ (split_oos + metrics_block, no migration of existing harnesses)

**Placeholder scan:** None — every code block is concrete and complete.

**Type consistency:**
- `strategy` / `dte_bucket` / `book` fields are `str | None = None` in both `Prediction` (Task 1) and TradeRecorder records (Task 1) and KBEntry (Phase 1, already shipped).
- `TradeRecorder.get_trades_by(*, strategy=None, dte_bucket=None, book=None, exit_reason=None)` is keyword-only.
- `split_oos(df, in_sample_fraction=0.60, date_col="date") -> tuple[in_sample, oos]` — note the `in_sample_fraction` name (not `oos_fraction`) avoids the ambiguity that bit some existing harnesses where `fraction=0.6` meant "first 60% is IS."
- `metrics_block(df) -> dict` returns the same shape as `hypothesis_runner._metrics_block` (Phase 1).

**Dependency order:** Task 1 (foundation tags) → Task 2 (extends `log_exit` + adds query helper, depends on Task 1's tag fields being on records) → Task 3 (independent, parallel).

**No live behavior change:** All three tasks are additive. Default values preserve every existing path. Paper broker hardcodes Phase-2-1 tags but doesn't change what trades it takes or how they execute.
