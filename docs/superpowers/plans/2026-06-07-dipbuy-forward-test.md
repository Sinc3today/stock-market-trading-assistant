# Dip-Buy Forward Paper-Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or inline). Steps use `- [ ]`.

**Goal:** Wire a live, paper-only forward test of the oversold dip-buy candidate into the bot — record a 1-ct bull-debit on each fresh RSI<30 cross, manage it (50% target / 10 trading-day hold), excluded from headline stats, surfaced in the EOD digest.

**Architecture:** A self-contained `learning/dipbuy_forward.py` (entry + resolver), reusing `dipbuy_signal_study.rsi_series` for the trigger, `OptionsLayer.analyze` for the structure (mirrors `shadow_tester`), and `exit_manager.bs_price` for daily marks. Two try/except daily jobs; core `ExitManager` untouched. New `candidate` book excluded from `/trades` stats.

**Tech Stack:** Python, pandas, pytest. **Spec:** `docs/superpowers/specs/2026-06-07-dipbuy-forward-test-design.md`.

---

## File Structure

- `learning/dipbuy_forward.py` — NEW. `is_fresh_oversold`, `maybe_open_dipbuy`, `_mark_spread`, `resolve_candidates`.
- `config.py` — MODIFY. `DIPBUY_FORWARD_*` flags.
- `journal/trade_recorder.py:391` — MODIFY. Exclude `candidate` from headline stats.
- `learning/scheduler.py` — MODIFY. Resolver job (16:12) + candidate section in `job_exit_digest`.
- `scheduler/spy_daily_scheduler.py` — MODIFY. Entry hook in the daily-play job (beside the shadow hook).
- `tests/test_dipbuy_forward.py` — NEW.

---

### Task 1: Config flags

**Files:** Modify `config.py`; Test `tests/test_dipbuy_forward.py`

- [ ] **Step 1: Failing test**
```python
def test_config_has_dipbuy_forward_flags():
    import config
    assert config.DIPBUY_FORWARD_ENABLED is True
    assert config.DIPBUY_FORWARD_DTE == 21
    assert config.DIPBUY_FORWARD_TARGET_PCT == 0.50
    assert config.DIPBUY_FORWARD_MAX_HOLD_TD == 10
```
- [ ] **Step 2: Run → FAIL** (`pytest tests/test_dipbuy_forward.py::test_config_has_dipbuy_forward_flags -v`)
- [ ] **Step 3: Append to `config.py`** (after the DIPBUY study block)
```python
# ── Dip-buy forward paper-test (2026-06-07) — LIVE, paper-only ───────────────
DIPBUY_FORWARD_ENABLED     = os.getenv("DIPBUY_FORWARD_ENABLED", "true").lower() == "true"
DIPBUY_FORWARD_DTE         = 21     # bull-call debit expiry at entry
DIPBUY_FORWARD_TARGET_PCT  = 0.50   # close at 50% of max profit
DIPBUY_FORWARD_MAX_HOLD_TD = 10     # ... or after 10 trading days held
DIPBUY_FORWARD_BOOK        = "candidate"
```
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** (`git add config.py tests/test_dipbuy_forward.py && git commit -m "feat: dip-buy forward-test config flags"`)

---

### Task 2: Exclude `candidate` book from headline stats

**Files:** Modify `journal/trade_recorder.py:391`; Test `tests/test_dipbuy_forward.py`

- [ ] **Step 1: Failing test**
```python
def test_candidate_book_excluded_from_summary_stats(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "TRADE_LOG_PATH", str(tmp_path / "t.json"), raising=False)
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    # one disciplined win, one candidate win
    rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="bull_debit",
                  strategy="bull_debit", direction="bullish", book="disciplined")
    cid = rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="bull_debit",
                        strategy="bull_debit", direction="bullish", book="candidate")
    rec.log_exit(cid, exit_price=5.0)   # big candidate "win"
    stats = rec.get_summary_stats()
    # candidate P&L must NOT appear in headline totals
    assert stats["total_trades"] <= 1 or all(
        t.get("book") != "candidate" for t in []  # candidate excluded from aggregation
    )
```
> NOTE to implementer: assert specifically that the candidate trade's P&L is excluded from `total_pnl`/win-rate. Adjust the assertion to the real `get_summary_stats` shape (see `journal/trade_recorder.py:379-413`); the existing shadow-exclusion test is the template — mirror it for `candidate`.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Edit `journal/trade_recorder.py:391`**
```python
        disciplined = [t for t in all_trades if t.get("book") not in ("shadow", "candidate")]
```
- [ ] **Step 4: Run → PASS** (also re-run the existing shadow-exclusion test — still green)
- [ ] **Step 5: Commit**

---

### Task 3: Trigger + entry (`is_fresh_oversold`, `maybe_open_dipbuy`)

**Files:** Create `learning/dipbuy_forward.py`; Test `tests/test_dipbuy_forward.py`

Mirror `learning/shadow_tester.py` (build via `OptionsLayer.analyze`, record via `recorder.log_entry`). Dependency-inject `options_layer` + `recorder` so tests use fakes.

- [ ] **Step 1: Failing tests**
```python
import pandas as pd
from types import SimpleNamespace

def _declining_df(n=60):
    closes = list(range(460, 460 - n, -1))   # steady decline → RSI<30
    idx = pd.bdate_range("2026-01-02", periods=n)
    return pd.DataFrame({"close": [float(c) for c in closes]}, index=idx)

def test_is_fresh_oversold_true_on_fresh_cross():
    from learning.dipbuy_forward import is_fresh_oversold
    df = _declining_df()
    assert isinstance(is_fresh_oversold(df), bool)

class _FakeLayer:
    def analyze(self, *a, **k):
        return {"strategy": "bull_debit", "legs": [{"action":"BUY","type":"call","strike":450},
                {"action":"SELL","type":"call","strike":460}],
                "entry_price": 4.0, "max_profit": 600.0, "max_loss": 400.0}

class _FakeRec:
    def __init__(self): self.entries = []
    def log_entry(self, **kw): self.entries.append(kw); return "TID123"
    def get_all_trades(self): return []
    def get_open_trades(self): return []
    def _save(self, t): pass

def test_maybe_open_records_one_candidate_on_trigger(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda df: True)
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out and out["recorded"] is True
    assert len(rec.entries) == 1
    e = rec.entries[0]
    assert e["book"] == "candidate" and e["size"] == 1
    assert e["dte_bucket"] == "dipbuy"

def test_maybe_open_noop_when_not_triggered(monkeypatch):
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda df: False)
    rec = _FakeRec()
    out = df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
                                   options_layer=_FakeLayer(), recorder=rec,
                                   today=pd.Timestamp("2026-03-02").date())
    assert out is None and rec.entries == []

def test_maybe_open_noop_when_disabled(monkeypatch):
    import config
    from learning import dipbuy_forward as df_mod
    monkeypatch.setattr(config, "DIPBUY_FORWARD_ENABLED", False)
    monkeypatch.setattr(df_mod, "is_fresh_oversold", lambda df: True)
    rec = _FakeRec()
    assert df_mod.maybe_open_dipbuy(_declining_df(), spot=450.0, ivr=30.0,
            options_layer=_FakeLayer(), recorder=rec,
            today=pd.Timestamp("2026-03-02").date()) is None
    assert rec.entries == []
```
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** `learning/dipbuy_forward.py`
```python
"""learning/dipbuy_forward.py -- LIVE paper-only forward test of the oversold
dip-buy candidate (docs/DIPBUY_STUDY.md). Records a 1-ct bull-debit on each
fresh RSI<30 cross into the 'candidate' book (headline-excluded) and manages it
(50% target / 10 trading-day hold). Core ExitManager untouched. Standing Rule #10."""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from datetime import date as _date
from loguru import logger
import config
from backtests.dipbuy_signal_study import rsi_series, oversold_triggers


def is_fresh_oversold(spy_df) -> bool:
    """True iff the latest daily bar is a FRESH RSI(14)<30 cross."""
    if spy_df is None or len(spy_df) < 30:
        return False
    trig = oversold_triggers(rsi_series(spy_df["close"].astype(float), 14), 30.0)
    return bool(trig.iloc[-1])


def maybe_open_dipbuy(spy_df, *, spot, ivr, options_layer, recorder, today=None):
    """On a fresh oversold cross, build a bull-call debit (~21 DTE) and record a
    1-ct paper trade in the 'candidate' book. Idempotent per day. Returns
    {'recorded': True, 'trade_id': tid} or None."""
    if not config.DIPBUY_FORWARD_ENABLED:
        return None
    if not is_fresh_oversold(spy_df):
        return None
    today = today or _date.today()
    # idempotency: skip if a candidate trade already opened today
    for t in recorder.get_open_trades():
        if t.get("book") == config.DIPBUY_FORWARD_BOOK and \
           str(t.get("entry_date", "")).startswith(today.isoformat()):
            return None
    score_result = {"final_score": 85, "direction": "bullish", "tier": "dipbuy"}
    target, stop = round(spot * 1.03, 2), round(spot * 0.98, 2)
    try:
        opts = options_layer.analyze("SPY", score_result, spot, target, stop,
                                     mode="swing", iv_rank=ivr,
                                     dte_target=config.DIPBUY_FORWARD_DTE)
    except Exception as e:
        logger.warning(f"dipbuy_forward: analyze failed: {e}")
        return None
    if not opts or not opts.get("legs"):
        logger.info("dipbuy_forward: no priceable bull structure today")
        return None
    from learning.paper_broker import AUTO_SOURCE
    tid = recorder.log_entry(
        ticker="SPY",
        entry_price=float(opts.get("entry_price") or opts.get("net_premium") or 1.0),
        size=1, trade_type=opts.get("strategy", "bull_debit"),
        strategy=opts.get("strategy", "bull_debit"), direction="bullish", mode="swing",
        legs=opts.get("legs", []), max_profit=opts.get("max_profit"),
        max_loss=opts.get("max_loss"),
        notes=f"[CANDIDATE {today.isoformat()}] oversold dip-buy forward-test",
        dte_bucket="dipbuy", book=config.DIPBUY_FORWARD_BOOK, source=AUTO_SOURCE)
    logger.info(f"dipbuy_forward: recorded candidate {tid} (entry_spot={spot})")
    return {"recorded": True, "trade_id": tid}
```
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

---

### Task 4: Resolver (`_mark_spread`, `resolve_candidates`)

**Files:** Modify `learning/dipbuy_forward.py`; Test `tests/test_dipbuy_forward.py`

- [ ] **Step 1: Failing tests**
```python
def test_mark_spread_bull_debit_value_rises_with_spot():
    from learning.dipbuy_forward import _mark_spread
    legs = [{"action":"BUY","type":"call","strike":450},
            {"action":"SELL","type":"call","strike":460}]
    low  = _mark_spread(legs, spot=448.0, vix=20.0, dte_days=10)
    high = _mark_spread(legs, spot=458.0, vix=20.0, dte_days=10)
    assert high > low and high >= 0.0

def test_resolve_closes_at_target():
    from learning import dipbuy_forward as df_mod
    closed = []
    class Rec:
        def get_open_trades(self):
            return [{"trade_id":"T1","book":"candidate","entry_price":4.0,"size":1,
                     "max_profit":600.0,"td_held":0,
                     "legs":[{"action":"BUY","type":"call","strike":450},
                             {"action":"SELL","type":"call","strike":460}]}]
        def log_exit(self, tid, exit_price, notes="", exit_reason=None):
            closed.append((tid, exit_price, exit_reason)); return True
        def _save(self, t): pass
        def get_all_trades(self): return self.get_open_trades()
    # spot high → mark near max → pnl >= 50% target → close
    out = df_mod.resolve_candidates(Rec(), spy_close=459.0, vix=18.0,
                                    today=pd.Timestamp("2026-03-20").date())
    assert len(out) == 1 and closed and closed[0][2] == "target"

def test_resolve_closes_at_max_hold():
    from learning import dipbuy_forward as df_mod
    closed = []
    class Rec:
        def get_open_trades(self):
            return [{"trade_id":"T2","book":"candidate","entry_price":4.0,"size":1,
                     "max_profit":600.0,"td_held":9,   # 9 → becomes 10 this run
                     "legs":[{"action":"BUY","type":"call","strike":450},
                             {"action":"SELL","type":"call","strike":460}]}]
        def log_exit(self, tid, exit_price, notes="", exit_reason=None):
            closed.append((tid, exit_reason)); return True
        def _save(self, t): pass
        def get_all_trades(self): return self.get_open_trades()
    out = df_mod.resolve_candidates(Rec(), spy_close=451.0, vix=18.0,
                                    today=pd.Timestamp("2026-03-20").date())
    assert closed and closed[0][1] == "time_stop"

def test_resolve_ignores_non_candidate_books():
    from learning import dipbuy_forward as df_mod
    class Rec:
        def get_open_trades(self): return [{"trade_id":"D","book":"disciplined"}]
        def log_exit(self, *a, **k): raise AssertionError("must not touch disciplined")
        def _save(self, t): pass
        def get_all_trades(self): return self.get_open_trades()
    assert df_mod.resolve_candidates(Rec(), spy_close=450.0, vix=18.0,
            today=pd.Timestamp("2026-03-20").date()) == []
```
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** (append to `learning/dipbuy_forward.py`)
```python
from learning.exit_manager import bs_price


def _mark_spread(legs, spot, vix, dte_days) -> float:
    """Net per-share value of the spread (long legs − short legs), BS off spot."""
    sigma = vix / 100.0
    t = max(dte_days, 0) / 365.0
    val = 0.0
    for leg in legs:
        otype = (leg.get("type") or leg.get("option_type") or "call").lower()
        p = bs_price(otype, spot, float(leg["strike"]), t, sigma)
        val += p if leg.get("action") == "BUY" else -p
    return val


def resolve_candidates(recorder, *, spy_close, vix, today=None):
    """Mark + close open 'candidate' trades: 50%-of-max-profit OR 10 td held.
    Wrapped by the caller per Standing Rule #10. Returns closed trade dicts."""
    today = today or _date.today()
    trades = recorder.get_all_trades()
    closed = []
    dirty = False
    for t in trades:
        if t.get("book") != config.DIPBUY_FORWARD_BOOK:
            continue
        if t.get("outcome") not in (None, "open"):
            continue
        td = int(t.get("td_held", 0)) + 1
        t["td_held"] = td
        dirty = True
        legs = t.get("legs") or []
        # remaining DTE from entry + configured expiry
        mark = _mark_spread(legs, spy_close, vix, dte_days=max(config.DIPBUY_FORWARD_DTE - td, 1))
        pnl = (mark - float(t.get("entry_price", 0.0))) * 100 * int(t.get("size", 1))
        mp = t.get("max_profit") or 0.0
        hit_target = mp > 0 and pnl >= config.DIPBUY_FORWARD_TARGET_PCT * mp
        hit_hold   = td >= config.DIPBUY_FORWARD_MAX_HOLD_TD
        if hit_target or hit_hold:
            reason = "target" if hit_target else "time_stop"
            recorder.log_exit(t["trade_id"], round(mark, 2),
                              notes=f"[CANDIDATE close {today.isoformat()}] {reason}",
                              exit_reason=reason)
            closed.append(t)
    if dirty:
        recorder._save(trades)
    return closed
```
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

---

### Task 5: Wiring — entry hook, resolver job, digest section

**Files:** Modify `scheduler/spy_daily_scheduler.py`, `learning/scheduler.py`; Test `tests/test_dipbuy_forward.py`

- [ ] **Step 1: Failing test (resolver job is registered + digest includes candidate)**
```python
def test_resolver_job_registered():
    import learning.scheduler as sch
    class FakeSched:
        def __init__(self): self.jobs=[]
        def add_job(self, fn, trig, **kw): self.jobs.append(kw.get("id"))
    s = FakeSched()
    sch.register_learning_jobs(s, polygon_client=None, post_fn=None)
    assert "learning_dipbuy_resolver" in s.jobs
```
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement**
  - In `learning/scheduler.py`: add `job_dipbuy_resolver(polygon_client, vix_client=None)` that fetches SPY daily close + VIX and calls `dipbuy_forward.resolve_candidates(TradeRecorder(), spy_close=..., vix=...)`, fully try/except. Register at `CronTrigger(day_of_week="mon-fri", hour=16, minute=12)` id `learning_dipbuy_resolver`.
  - Extend `job_exit_digest`: after the disciplined section, gather today's `candidate`-book closes (same `exit_date.startswith(today)` filter) and, if any, append a `"📕 Forward-test (candidate): N closed (±$net)"` section to the same push body.
  - In `scheduler/spy_daily_scheduler.py`, beside `_run_daily_shadow(...)`: add a try/except `_run_daily_dipbuy(...)` that fetches SPY daily history via `polygon_client.get_bars("SPY", timeframe="day", limit=260)` and calls `dipbuy_forward.maybe_open_dipbuy(spy_df, spot=_spot, ivr=_ivr, options_layer=OptionsLayer(), recorder=TradeRecorder())`.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

---

### Task 6: Full verification + deploy

- [ ] **Step 1: Full offline suite**
Run: `pytest tests/ -q -m "not integration" --deselect tests/test_fred.py --deselect tests/test_economic_scanner.py`
Expected: all pass.
- [ ] **Step 2: Import + wiring smoke**
```bash
python -c "import learning.dipbuy_forward, learning.scheduler, scheduler.spy_daily_scheduler; print('ok')"
```
- [ ] **Step 3: Commit any fixups; finish branch (merge to main) per finishing-a-development-branch.**
- [ ] **Step 4: Deploy** `systemctl --user restart trader.service`; verify `NRestarts=0`, "✅ All systems running", no traceback, and that the new jobs registered.

---

## Self-Review

- **Spec coverage:** candidate book (T1/T2), trigger+entry mirroring shadow (T3), isolated resolver with 50%/10td exit (T4), entry hook + 16:12 resolver + digest section (T5), verify+deploy (T6). Core ExitManager untouched ✔. Kill-switch ✔.
- **Placeholders:** none — concrete code per step (T2's assertion is flagged for the implementer to match the real stats shape, with the shadow test as template).
- **Type consistency:** `is_fresh_oversold`/`maybe_open_dipbuy`/`_mark_spread`/`resolve_candidates` signatures consistent across tasks + tests; `book="candidate"`, `dte_bucket="dipbuy"`, `td_held` counter used consistently.
