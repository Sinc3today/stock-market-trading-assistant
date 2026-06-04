# Extension-Gate Shadow-Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On extension-gate skip days, record + score the counterfactual bull trade (directional + a priced `book="shadow"` paper trade) and, when the shadow beats the gate over N days, let the hypothesis engine propose relaxing `EXTENDED_TREND_MAX_PCT`.

**Architecture:** A new isolated `learning/shadow_tester.py` is invoked from the daily-play scheduler only on the extension-skip regime; it builds the would-be bull structure via the existing `OptionsLayer` and records a `book="shadow"`/`source="auto-paper"` trade that rides the existing exit/expiry lifecycle. `outcome_resolver` stamps the same-day directional result; the hypothesis engine reads `shadow_stats()` to propose relaxing the (now-tunable) cap.

**Tech Stack:** Python 3.11, pytest, loguru. Reuses `signals/options_layer`, `journal/trade_recorder`, `learning/outcome_resolver`, `learning/hypothesis_engine`, `scheduler/spy_daily_scheduler`.

**Spec:** `docs/superpowers/specs/2026-06-03-extension-gate-shadow-test-design.md`

---

## Reference facts (verified — do not re-derive)

- **Extension gate:** `signals/regime_detector.py:283` — when `ma_dist_pct > EXTENDED_TREND_MAX_PCT` (=9.0) it returns `RegimeResult(Regime.TRENDING_UP_CALM, tradeable=False, recommendation="SKIP — trend too extended (wait for pullback)", ...)`. `RegimeResult` (dataclass, `regime_detector.py:~57`) has `.regime` (a `Regime` enum), `.tradeable` (bool), `.recommendation` (str), `.reasons` (list[str]), `.metrics` (dict, includes `vix`, `ivr`, `spy_close`, `ma200`...). `Regime.TRENDING_UP_CALM` is the enum member.
- **The would-be play (regime_detector.py:296-313):** bull put credit spread when `ivr_current >= 50 and not config.PREFER_DEBIT_OVER_CREDIT`; else bull call debit spread.
- **`OptionsLayer.analyze(ticker, score_result, stock_price, target, stop, mode="swing", iv_rank=None, iv_current=None, dte_target=None) -> dict`** returns `{strategy, legs, max_profit, max_loss, ...}`. `signals/spy_daily_strategy.py:153-161` shows how the daily play calls it with a synthetic `score_result` — READ that block and mirror it for the bull case (direction "bullish").
- **`TradeRecorder.log_entry(...)`** accepts `book=` and `source=` kwargs (e.g. `book="shadow"`, `source=AUTO_SOURCE`); persists to `logs/trades.json`. `AUTO_SOURCE = "auto-paper"` in `learning.paper_broker`. `get_all_trades()` returns the list.
- **`outcome_resolver.OutcomeResolver._score(direction, entry, close) -> "correct"|"wrong"|"partial"`** (static). `resolve_today` skip-branch (`outcome_resolver.py`, the `if not prediction.get("tradeable"):` block) already fetches `spy_close = self._fetch_spy_close()`.
- **`hypothesis_engine.TUNABLE_PARAMS`** = `{(module_str, var_str): {"type": "float"|"int", "min": x, "max": y}}`. `_is_valid_param` checks bounds. The engine builds a proposal context dict in `_build_context`/`reflect`-style; READ `learning/hypothesis_engine.py` for where context is assembled.
- **Daily job:** `scheduler/spy_daily_scheduler.py::job_spy_premarket(...)` (line 44) runs at 09:15 ET, wrapped in try/except; it has `polygon_client`, `vix_client`, `ivr_client`, `event_calendar`. It builds the brief via `MorningBriefer`/`SPYDailyStrategy`. This is where `run_shadow` is invoked.
- `is_auto_paper(t)` (learning.paper_broker) → exit_manager/expiry_resolver pick up `source="auto-paper"` trades regardless of book, so a `book="shadow"` trade is managed/closed automatically.

**Pre-flight:**
```bash
cd /home/nexus/Projects/stock-market-trading-assistant
source .venv/bin/activate
pytest tests/ -k "regime or options_layer or outcome or hypothesis or trade_recorder or shadow" -p no:cacheprovider -q | tail -3
git status --short   # clean; git checkout -b extension-gate-shadow-test
```

---

## Task 1: config flags + `_is_extension_skip` predicate

**Files:**
- Modify: `config.py`
- Create: `learning/shadow_tester.py`
- Test: `tests/test_shadow_tester.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shadow_tester.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from types import SimpleNamespace
from signals.regime_detector import Regime
from learning.shadow_tester import _is_extension_skip


def _rr(regime, tradeable, recommendation):
    return SimpleNamespace(regime=regime, tradeable=tradeable, recommendation=recommendation, reasons=[], metrics={})


def test_extension_skip_detected():
    rr = _rr(Regime.TRENDING_UP_CALM, False, "SKIP — trend too extended (wait for pullback)")
    assert _is_extension_skip(rr) is True


def test_tradeable_day_not_extension_skip():
    rr = _rr(Regime.TRENDING_UP_CALM, True, "BULL CALL DEBIT SPREAD — buy the directional move")
    assert _is_extension_skip(rr) is False


def test_other_skip_reason_not_extension_skip():
    rr = _rr(Regime.UNKNOWN, False, "SKIP — SPY too close to 200MA, direction unclear")
    assert _is_extension_skip(rr) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shadow_tester.py -k extension_skip -q`
Expected: FAIL — `No module named 'learning.shadow_tester'`

- [ ] **Step 3: Write minimal implementation**

Add to `config.py` (near the other learning-loop flags):
```python
# Extension-gate shadow-test: on extension-skip days, paper-trade the bull play
# the gate refused (book="shadow") + score the directional counterfactual; the
# hypothesis engine proposes relaxing EXTENDED_TREND_MAX_PCT when the shadow
# beats the gate over SHADOW_MIN_DAYS at >= SHADOW_MIN_WINRATE.
SHADOW_TEST_ENABLED = True
SHADOW_MIN_DAYS     = 10
SHADOW_MIN_WINRATE  = 0.55
```

Create `learning/shadow_tester.py`:
```python
"""learning/shadow_tester.py -- Extension-gate shadow-test (anti-bias).

On a day the regime's extension gate forces SKIP, record + score the
counterfactual bull trade the gate refused. See
docs/superpowers/specs/2026-06-03-extension-gate-shadow-test-design.md.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

import config
from signals.regime_detector import Regime

SHADOW_BOOK = "shadow"


def _is_extension_skip(regime_result) -> bool:
    """True only for the extension-gate skip (TRENDING_UP_CALM, not tradeable,
    reason mentions over-extension)."""
    rec = (getattr(regime_result, "recommendation", "") or "").lower()
    return (getattr(regime_result, "regime", None) == Regime.TRENDING_UP_CALM
            and not getattr(regime_result, "tradeable", True)
            and ("extended" in rec or "extension" in rec))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shadow_tester.py -k extension_skip -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add config.py learning/shadow_tester.py tests/test_shadow_tester.py
git commit -m "feat: shadow-test config flags + _is_extension_skip predicate"
```

---

## Task 2: `run_shadow` — build + record the shadow bull trade

**Files:**
- Modify: `learning/shadow_tester.py`
- Test: `tests/test_shadow_tester.py`

> **Implementer:** read `signals/spy_daily_strategy.py:140-180` to see exactly how the daily play builds the synthetic `score_result` and calls `OptionsLayer.analyze` (target/stop derivation, dte_target, iv_rank). Mirror that for the bullish case in `_build_shadow_options`. Use dependency injection: `run_shadow` takes an `options_layer`, a `trade_recorder`, and the data values (spot, ivr) so tests inject fakes — do NOT hit live clients in the function body.

- [ ] **Step 1: Write the failing test**

```python
def test_run_shadow_records_shadow_trade_on_extension_skip(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "SHADOW_TEST_ENABLED", True)
    monkeypatch.setattr(config, "PREFER_DEBIT_OVER_CREDIT", False)
    from datetime import date
    from journal.trade_recorder import TradeRecorder
    from learning.shadow_tester import run_shadow
    from signals.regime_detector import Regime

    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended (wait for pullback)",
                         reasons=[], metrics={"spy_close": 760.0, "ivr": 55.0})

    class _FakeLayer:
        def analyze(self, *a, **k):
            return {"strategy": "credit_spread", "direction": "bullish",
                    "legs": [{"action": "SELL", "type": "put", "strike": 755},
                             {"action": "BUY", "type": "put", "strike": 750}],
                    "max_profit": 120.0, "max_loss": 380.0, "net_premium": 1.2}

    rec = TradeRecorder()
    out = run_shadow(rr, spot=760.0, ivr=55.0, options_layer=_FakeLayer(),
                     trade_recorder=rec, today=date(2026, 6, 3))
    assert out is not None and out["recorded"] is True
    t = [x for x in rec.get_all_trades() if x.get("book") == "shadow"]
    assert len(t) == 1
    assert t[0]["source"] == "auto-paper"
    assert t[0]["strategy"] == "credit_spread"
    assert t[0].get("entry_spy") == 760.0       # recorded for directional scoring


def test_run_shadow_returns_none_when_not_extension_skip():
    from learning.shadow_tester import run_shadow
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=True,
                         recommendation="BULL CALL DEBIT SPREAD", reasons=[], metrics={})
    assert run_shadow(rr, spot=760.0, ivr=55.0, options_layer=object(),
                      trade_recorder=object()) is None


def test_run_shadow_disabled_returns_none(monkeypatch):
    import config
    monkeypatch.setattr(config, "SHADOW_TEST_ENABLED", False)
    from learning.shadow_tester import run_shadow
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended", reasons=[], metrics={})
    assert run_shadow(rr, spot=760.0, ivr=55.0, options_layer=object(),
                      trade_recorder=object()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shadow_tester.py -k run_shadow -q`
Expected: FAIL — `cannot import name 'run_shadow'`

- [ ] **Step 3: Write minimal implementation**

Add to `learning/shadow_tester.py`:
```python
from datetime import date as _date


def _build_shadow_options(options_layer, spot: float, ivr: float) -> dict | None:
    """Build the would-be bull structure (mirrors SPYDailyStrategy's bull path):
    bull put credit if IVR>=50 & not PREFER_DEBIT, else bull call debit."""
    target = round(spot * 1.03, 2)   # mirror the daily play's target/stop bands
    stop   = round(spot * 0.98, 2)
    score_result = {"final_score": 85, "direction": "bullish"}
    try:
        return options_layer.analyze("SPY", score_result, spot, target, stop,
                                     mode="swing", iv_rank=ivr, dte_target=45)
    except Exception as e:
        logger.warning(f"shadow: OptionsLayer.analyze failed: {e}")
        return None


def run_shadow(regime_result, *, spot, ivr, options_layer, trade_recorder,
               today=None) -> dict | None:
    """If today is an extension-skip, build + record the counterfactual bull
    trade as a book='shadow' paper position. Returns a result dict or None."""
    if not config.SHADOW_TEST_ENABLED:
        return None
    if not _is_extension_skip(regime_result):
        return None
    opts = _build_shadow_options(options_layer, spot, ivr)
    if not opts or not opts.get("legs"):
        logger.info("shadow: no priceable bull structure today — no shadow trade")
        return None
    today = today or _date.today()
    from learning.paper_broker import AUTO_SOURCE
    tid = trade_recorder.log_entry(
        ticker      = "SPY",
        entry_price = float(opts.get("net_premium") or opts.get("entry_price") or 1.0),
        size        = 1,
        trade_type  = opts.get("strategy", "credit_spread"),
        strategy    = opts.get("strategy", "credit_spread"),
        direction   = "bullish",
        mode        = "swing",
        legs        = opts.get("legs", []),
        max_profit  = opts.get("max_profit"),
        max_loss    = opts.get("max_loss"),
        notes       = f"[SHADOW {today.isoformat()}] extension-gate counterfactual bull play",
        dte_bucket  = "45DTE",
        book        = SHADOW_BOOK,
        source      = AUTO_SOURCE,
    )
    # entry_spy for directional scoring (outcome_resolver stamps the result at EOD)
    trades = trade_recorder.get_all_trades()
    for t in trades:
        if t.get("trade_id") == tid:
            t["entry_spy"] = float(spot)
    trade_recorder._save(trades)
    logger.info(f"shadow: recorded counterfactual bull trade {tid} (book=shadow)")
    return {"recorded": True, "trade_id": tid}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shadow_tester.py -k run_shadow -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add learning/shadow_tester.py tests/test_shadow_tester.py
git commit -m "feat: run_shadow builds+records the extension-gate counterfactual bull trade"
```

---

## Task 3: `shadow_stats` — rolling expectancy

**Files:**
- Modify: `learning/shadow_tester.py`
- Test: `tests/test_shadow_tester.py`

- [ ] **Step 1: Write the failing test**

```python
def test_shadow_stats_aggregates_shadow_book(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    from learning.shadow_tester import shadow_stats
    rec = TradeRecorder()
    # two closed shadow trades (1 win, 1 loss) + one disciplined (ignored)
    rec.log_entry(ticker="SPY", entry_price=1.2, size=1, trade_type="credit_spread",
                  strategy="credit_spread", book="shadow", source="auto-paper",
                  legs=[{"action": "SELL", "type": "put", "strike": 755}])
    rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="credit_spread",
                  strategy="credit_spread", book="shadow", source="auto-paper",
                  legs=[{"action": "SELL", "type": "put", "strike": 750}])
    rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="iron_condor",
                  strategy="iron_condor", book="disciplined", source="auto-paper")
    # close the two shadow trades with known P&L + stamp directional
    trades = rec.get_all_trades()
    sh = [t for t in trades if t["book"] == "shadow"]
    sh[0]["outcome"] = "win";  sh[0]["pnl_dollars"] = 80.0;  sh[0]["shadow_directional"] = "correct"
    sh[1]["outcome"] = "loss"; sh[1]["pnl_dollars"] = -40.0; sh[1]["shadow_directional"] = "wrong"
    rec._save(trades)

    s = shadow_stats(n_days=3650, trade_recorder=rec)
    assert s["n"] == 2
    assert s["closed_pnl"] == 40.0                 # 80 - 40
    assert s["directional_win_rate"] == 0.5        # 1 of 2 correct


def test_shadow_stats_empty_is_neutral(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    from learning.shadow_tester import shadow_stats
    s = shadow_stats(n_days=30, trade_recorder=TradeRecorder())
    assert s["n"] == 0 and s["closed_pnl"] == 0.0 and s["directional_win_rate"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shadow_tester.py -k shadow_stats -q`
Expected: FAIL — `cannot import name 'shadow_stats'`

- [ ] **Step 3: Write minimal implementation**

Add to `learning/shadow_tester.py`:
```python
def shadow_stats(n_days: int = 30, *, trade_recorder=None) -> dict:
    """Rolling expectancy over book='shadow' trades. Neutral (n=0) when none."""
    if trade_recorder is None:
        from journal.trade_recorder import TradeRecorder
        trade_recorder = TradeRecorder()
    shadow = [t for t in trade_recorder.get_all_trades() if t.get("book") == SHADOW_BOOK]
    closed = [t for t in shadow if t.get("outcome") in ("win", "loss", "breakeven")]
    closed_pnl = round(sum(t.get("pnl_dollars") or 0.0 for t in closed), 2)
    scored = [t for t in shadow if t.get("shadow_directional") in ("correct", "wrong")]
    correct = sum(1 for t in scored if t.get("shadow_directional") == "correct")
    win_rate = round(correct / len(scored), 3) if scored else 0.0
    return {
        "n":                    len(shadow),
        "n_closed":             len(closed),
        "closed_pnl":           closed_pnl,
        "open_mtm":             0.0,
        "directional_win_rate": win_rate,
    }
```
(NOTE: `n_days` is accepted for the interface but the v1 aggregate is over all shadow trades; a date-window filter is a noted follow-up — keep the param so callers are stable.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shadow_tester.py -k shadow_stats -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add learning/shadow_tester.py tests/test_shadow_tester.py
git commit -m "feat: shadow_stats rolling expectancy over the shadow book"
```

---

## Task 4: `outcome_resolver` stamps the same-day directional result

**Files:**
- Modify: `learning/outcome_resolver.py` (the skip branch)
- Test: `tests/test_outcome_resolver_shadow.py` (new)

> **Implementer:** read `resolve_today`'s `if not prediction.get("tradeable"):` block. After it marks the skip + snapshots, ADD: for any `book="shadow"` trade entered today that lacks `shadow_directional`, stamp `shadow_directional = OutcomeResolver._score("bullish", entry_spy, spy_close)`. Use `self.trades` (the TradeRecorder). Guard on `spy_close` and `entry_spy` present. Do NOT change the real prediction's `skip` status.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_outcome_resolver_shadow.py
import os, sys
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def test_skip_day_stamps_shadow_directional(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.predictions import PredictionLog
    from learning.outcome_resolver import OutcomeResolver
    rec = TradeRecorder()
    tid = rec.log_entry(ticker="SPY", entry_price=1.2, size=1, trade_type="credit_spread",
                        strategy="credit_spread", book="shadow", source="auto-paper",
                        legs=[{"action": "SELL", "type": "put", "strike": 755}])
    trades = rec.get_all_trades()
    for t in trades:
        if t["trade_id"] == tid:
            t["entry_spy"] = 760.0
            t["entry_date"] = "2026-06-03 09:16 AM EST"
    rec._save(trades)
    preds = PredictionLog()
    preds.log({"date": "2026-06-03", "direction": "bullish", "tradeable": False,
               "entry_spy": 760.0, "confidence": 0.0})

    class _Poly:
        def get_bars(self, *a, **k):
            import pandas as pd
            return pd.DataFrame({"close": [766.0]})   # SPY closed UP → bullish correct

    OutcomeResolver(polygon_client=_Poly(), trade_recorder=rec,
                    prediction_log=preds).resolve_today(today=date(2026, 6, 3))
    t = rec.get_trade_by_id(tid)
    assert t["shadow_directional"] == "correct"   # 766 > 760 entry
```

(If `PredictionLog.log` has a different method name/shape, read `learning/predictions.py` and adapt the fixture; the assertion on `shadow_directional` is the contract.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_outcome_resolver_shadow.py -q`
Expected: FAIL — `shadow_directional` not set (KeyError/None).

- [ ] **Step 3: Write minimal implementation**

In `learning/outcome_resolver.py`, inside the `if not prediction.get("tradeable"):` block, after `self._snapshot_open_paper_trades(today_str, spy_close)` and before its `return`, add:
```python
            self._stamp_shadow_directional(today_str, spy_close)
```
And add the method to `OutcomeResolver`:
```python
    def _stamp_shadow_directional(self, today_str: str, spy_close: float | None) -> None:
        """On an extension-skip day, score the shadow trade's bullish
        counterfactual (SPY close vs entry_spy). Reuses _score; does not touch
        the real prediction's skip status."""
        if spy_close is None:
            return
        trades = self.trades.get_all_trades()
        changed = False
        for t in trades:
            if t.get("book") != "shadow":
                continue
            if t.get("shadow_directional"):
                continue
            if (t.get("entry_date") or "")[:10] != today_str:
                continue
            entry_spy = t.get("entry_spy")
            if not isinstance(entry_spy, (int, float)):
                continue
            t["shadow_directional"] = self._score("bullish", entry_spy, spy_close)
            changed = True
        if changed:
            self.trades._save(trades)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_outcome_resolver_shadow.py tests/ -k "outcome" -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add learning/outcome_resolver.py tests/test_outcome_resolver_shadow.py
git commit -m "feat: outcome_resolver stamps shadow directional on extension-skip days"
```

---

## Task 5: Hypothesis engine — tunable cap + shadow-pressure proposal gate

**Files:**
- Modify: `learning/hypothesis_engine.py`
- Test: `tests/test_hypothesis_shadow.py` (new)

> **Implementer:** read `learning/hypothesis_engine.py` — `TUNABLE_PARAMS`, how the proposal context is built, and how proposals are validated. Add the tunable entry and feed `shadow_stats()` into the context with a boolean flag the prompt/logic uses to permit a raise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hypothesis_shadow.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_extended_trend_cap_is_tunable():
    from learning.hypothesis_engine import TUNABLE_PARAMS
    rule = TUNABLE_PARAMS[("signals.regime_detector", "EXTENDED_TREND_MAX_PCT")]
    assert rule["type"] == "float"
    assert rule["min"] == 9.0 and rule["max"] == 15.0   # raise-only band, never below backtested 9.0


def test_shadow_pressure_flag(monkeypatch):
    import config
    monkeypatch.setattr(config, "SHADOW_MIN_DAYS", 10)
    monkeypatch.setattr(config, "SHADOW_MIN_WINRATE", 0.55)
    from learning.hypothesis_engine import shadow_under_pressure
    assert shadow_under_pressure({"n": 12, "closed_pnl": 300.0, "directional_win_rate": 0.6}) is True
    assert shadow_under_pressure({"n": 12, "closed_pnl": -50.0, "directional_win_rate": 0.6}) is False
    assert shadow_under_pressure({"n": 5,  "closed_pnl": 300.0, "directional_win_rate": 0.6}) is False
    assert shadow_under_pressure({"n": 12, "closed_pnl": 300.0, "directional_win_rate": 0.5}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hypothesis_shadow.py -q`
Expected: FAIL — key missing / `shadow_under_pressure` undefined.

- [ ] **Step 3: Write minimal implementation**

In `learning/hypothesis_engine.py`:
- Add to `TUNABLE_PARAMS`:
  ```python
      ("signals.regime_detector", "EXTENDED_TREND_MAX_PCT"): {"type": "float", "min": 9.0, "max": 15.0},
  ```
- Add a module-level helper:
  ```python
  def shadow_under_pressure(stats: dict) -> bool:
      """The extension gate is under disconfirming pressure when the shadow book
      has positive P&L AND a winning directional rate over enough days."""
      return (stats.get("n", 0) >= config.SHADOW_MIN_DAYS
              and (stats.get("closed_pnl") or 0.0) > 0
              and (stats.get("directional_win_rate") or 0.0) >= config.SHADOW_MIN_WINRATE)
  ```
- In the proposal-context assembly (read the method that builds the LLM context), add the shadow stats + a directive when under pressure:
  ```python
  from learning.shadow_tester import shadow_stats
  _sh = shadow_stats()
  ctx["shadow_stats"] = _sh
  if shadow_under_pressure(_sh):
      ctx["shadow_directive"] = ("The extension gate (EXTENDED_TREND_MAX_PCT) is under "
                                 "disconfirming pressure: the shadow book it skips is profitable "
                                 f"({_sh['closed_pnl']:+.0f}, win-rate {_sh['directional_win_rate']:.0%} "
                                 f"over {_sh['n']}). You MAY propose RAISING EXTENDED_TREND_MAX_PCT "
                                 "within its tunable band.")
  ```
  (Match the actual context dict variable name in the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hypothesis_shadow.py tests/ -k hypothesis -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add learning/hypothesis_engine.py tests/test_hypothesis_shadow.py
git commit -m "feat: EXTENDED_TREND_MAX_PCT tunable + shadow-pressure proposal gate"
```

---

## Task 6: Wire `run_shadow` into the daily scheduler job

**Files:**
- Modify: `scheduler/spy_daily_scheduler.py`
- Test: `tests/test_spy_scheduler_shadow.py` (new)

> **Implementer:** read `job_spy_premarket` fully. It builds the brief and has `polygon_client`/`vix_client`/`ivr_client`. After the brief is built (so the regime is known), obtain the `RegimeResult`, the current SPY spot, and the IVR, construct an `OptionsLayer(options_chain=OptionsChain())` and a `TradeRecorder()`, and call `run_shadow(regime_result, spot=spot, ivr=ivr, options_layer=ol, trade_recorder=rec)` INSIDE its OWN try/except so a shadow failure can't disturb the real daily play. If the brief object doesn't directly expose the `RegimeResult`, get it from `SPYDailyStrategy(...).detector` / the brief's regime fields — read the code and use the real accessor. Extract the shadow invocation into a small helper `_run_daily_shadow(...)` so it's unit-testable without the whole job.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spy_scheduler_shadow.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from types import SimpleNamespace
from unittest import mock


def test_run_daily_shadow_invokes_run_shadow_on_extension_skip(monkeypatch):
    import scheduler.spy_daily_scheduler as sch
    calls = []
    monkeypatch.setattr(sch, "run_shadow",
                        lambda rr, **kw: calls.append(kw) or {"recorded": True})
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended",
                         reasons=[], metrics={"spy_close": 760.0, "ivr": 55.0})
    sch._run_daily_shadow(rr, spot=760.0, ivr=55.0)
    assert len(calls) == 1
    assert calls[0]["spot"] == 760.0 and calls[0]["ivr"] == 55.0


def test_run_daily_shadow_swallows_errors(monkeypatch):
    import scheduler.spy_daily_scheduler as sch
    def boom(rr, **kw):
        raise RuntimeError("shadow blew up")
    monkeypatch.setattr(sch, "run_shadow", boom)
    from signals.regime_detector import Regime
    rr = SimpleNamespace(regime=Regime.TRENDING_UP_CALM, tradeable=False,
                         recommendation="SKIP — trend too extended", reasons=[], metrics={})
    # must NOT raise (the real daily play must never be disturbed)
    sch._run_daily_shadow(rr, spot=760.0, ivr=55.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_spy_scheduler_shadow.py -q`
Expected: FAIL — `_run_daily_shadow` / `run_shadow` not importable from the module.

- [ ] **Step 3: Write minimal implementation**

In `scheduler/spy_daily_scheduler.py`:
- Add imports near the top: `from learning.shadow_tester import run_shadow`; `from signals.options_layer import OptionsLayer`; `from data.options_chain import OptionsChain`; `from journal.trade_recorder import TradeRecorder`.
- Add the helper:
  ```python
  def _run_daily_shadow(regime_result, *, spot, ivr):
      """Invoke the extension-gate shadow-test, isolated so a failure can never
      disturb the real daily play (Standing Rule #10)."""
      try:
          run_shadow(regime_result, spot=spot, ivr=ivr,
                     options_layer=OptionsLayer(options_chain=OptionsChain()),
                     trade_recorder=TradeRecorder())
      except Exception as e:
          logger.warning(f"shadow-test failed (ignored): {e}")
  ```
- In `job_spy_premarket`, after the brief/regime is computed, call `_run_daily_shadow(regime_result, spot=<spy spot>, ivr=<ivr>)` using the real regime/spot/ivr accessors from the surrounding code.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_spy_scheduler_shadow.py -q && python -c "import scheduler.spy_daily_scheduler"`
Expected: PASS (2 passed) + clean import.

- [ ] **Step 5: Commit**

```bash
git add scheduler/spy_daily_scheduler.py tests/test_spy_scheduler_shadow.py
git commit -m "feat: wire run_shadow into the daily job on extension-skip (error-isolated)"
```

---

## Final verification

- [ ] **Offline gate:**
```bash
pytest tests/ -k "shadow or regime or outcome or hypothesis or options_layer or trade_recorder or spy_scheduler" -p no:cacheprovider -q | tail -5
```
Expected: all green (pre-existing live-FRED tests excluded by this selection).

- [ ] **Update BUILD_LOG.md** (shadow-test: book="shadow", directional + priced, hypothesis-engine relax proposal, EXTENDED_TREND_MAX_PCT tunable; deploy = restart).

- [ ] **Deploy:** restart `smta.service`; on the next extension-skip day a `book="shadow"` trade should open + get directional-stamped at 16:05.

---

## Self-review (completed by author)

- **Spec coverage:** `_is_extension_skip` (T1) + config flags (T1); `run_shadow` builds bull structure + records book="shadow"/source=auto-paper + entry_spy (T2); directional scoring via outcome_resolver (T4); shadow book lifecycle is free (source=auto-paper → exit/expiry); `shadow_stats` (T3); hypothesis-engine tunable + pressure gate (T5); scheduler wiring error-isolated (T6). All spec sections map to a task.
- **Placeholder scan:** none — complete code in each step; T4/T5/T6 carry "read the surrounding code" notes (they edit large existing files) but the inserted code is complete.
- **Type consistency:** `run_shadow(regime_result, *, spot, ivr, options_layer, trade_recorder, today=None)` consistent T2→T6; `shadow_stats(n_days, *, trade_recorder=None) -> {n, n_closed, closed_pnl, open_mtm, directional_win_rate}` consistent T3→T5; `_is_extension_skip(regime_result)` T1→T2; `book="shadow"` / `shadow_directional` field names consistent T2/T3/T4; `shadow_under_pressure(stats)` reads the same stats keys T5↔T3.
```
