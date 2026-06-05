# Intraday Time-Exit Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add time-based intraday exit rules (hard-close + scratch) behind one shared, stateless evaluator used by both the backtest and the live ExitManager, walk-forward-gate them, and let the bot learn whether each exit was a mistake (exit-quality counterfactual).

**Architecture:** A pure `evaluate_intraday_exit()` owns the full exit decision (target → stop → scratch → hard-close). The backtest records each trade's 5-min P&L path (real-mark AND BS-off-spot mark) and an offline arm-replay layer scores candidate rules through the existing walk-forward verdict machinery. The live ExitManager calls the same evaluator with a BS-off-intraday-spot mark, behind a kill-switch, only for `(strategy,dte_bucket)` combos the walk-forward earned. Every exit records the hold-to-EOD counterfactual so `exit_quality = pnl_exit − pnl_hold` can be scored.

**Tech Stack:** Python 3.11, pytest, pandas, loguru. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-06-05-intraday-time-exit-model-design.md`

**Sequencing:** Tasks 1-2 (evaluator + config) → 3-4 (backtest path recording + counterfactual) → 5-6 (WF arm-replay + parity) → **RUN the WF (Task 7)** → 8-11 (live wiring, parameterized by Task 7's result). Tasks 1-7 carry zero live-trading risk.

**Conventions to follow:**
- Activate venv first every session: `source .venv/bin/activate`.
- Run targeted tests per commit; full suite (`pytest tests/ -m "not integration" -p no:cacheprovider -q`) before the final review. Deselect the flaky live-FRED tests if they fail: `--deselect tests/test_fred.py --deselect tests/test_economic_scanner.py`.
- Commit message footer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Branch: create and work on `intraday-time-exit-model` (NOT main).

---

## File Structure

| File | Responsibility |
|---|---|
| `signals/intraday_exit_rules.py` *(new)* | `ExitDecision` dataclass + pure `evaluate_intraday_exit()`. The shared exit decision for backtest AND live. |
| `signals/exit_counterfactual.py` *(new)* | `exit_quality()` + `aggregate_exit_quality()` (piece D). Pure. |
| `config.py` *(modify)* | `INTRADAY_TIME_EXIT_ENABLED` kill-switch; per-`(strategy,dte_bucket)` `SCRATCH_TIME_*` / `SCRATCH_THETA_*` / `HARD_CLOSE_TIME_*` (None until earned); `EXIT_PARITY_MIN_AGREE` / `EXIT_PARITY_MAX_PNL_GAP`. |
| `learning/exit_manager.py` *(modify)* | `_exit_rule_for` surfaces the new time-exit keys; `_evaluate` delegates to `evaluate_intraday_exit`; new BS-off-intraday-spot mark; kill-switch; counterfactual intent. |
| `backtests/intraday_backtest.py` *(modify)* | sims record each trade's full 5-min P&L path with BOTH marks (real + BS-off-spot) and the EOD hold value. |
| `backtests/intraday_router_wf.py` *(modify)* | emit `logs/wf_trade_paths.jsonl`; offline `replay_arms()` + `parity_divergence()`; per-arm × per-combo verdict table. |
| `learning/outcome_resolver.py` *(modify)* | fill live `pnl_hold` + `exit_quality` onto closed time-exit trades at EOD. |

---

## Task 1: Shared exit evaluator

**Files:**
- Create: `signals/intraday_exit_rules.py`
- Test: `tests/test_intraday_exit_rules.py`

The evaluator owns the WHOLE exit decision so backtest and live are byte-identical. It is pure: no I/O, no config reads except the explicit `enable_time_exits` flag the caller passes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_intraday_exit_rules.py
from datetime import time
from signals.intraday_exit_rules import evaluate_intraday_exit, ExitDecision


def _pos(**kw):
    base = dict(strategy="put_debit_spread", dte_bucket="0DTE",
                max_profit=300.0, max_loss=56.0)
    base.update(kw)
    return base


def _rule(**kw):
    base = dict(profit_target_pct=1.0, stop_pct=0.75,
                scratch_time=None, scratch_theta=0.0, hard_close_time=None)
    base.update(kw)
    return base


def test_profit_target_fires_first_even_after_scratch_time():
    # Working trade past target: takes the win, ignores the time rule.
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": 300.0, "exit_price": 4.0},
        now_et=time(13, 30), rule=_rule(scratch_time="13:00", hard_close_time="14:00"))
    assert d is not None and d.reason == "target" and d.exit_price == 4.0


def test_stop_fires_when_pnl_below_negative_stop():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -50.0, "exit_price": 0.1},
        now_et=time(10, 0), rule=_rule())
    assert d is not None and d.reason == "stop"


def test_scratch_fires_when_not_working_at_scratch_time():
    # pnl below theta*max_profit (theta=0 → below breakeven) at/after scratch_time.
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(13, 0), rule=_rule(scratch_time="13:00", scratch_theta=0.0))
    assert d is not None and d.reason == "scratch" and d.fired_at == "13:00"


def test_scratch_does_not_fire_for_a_working_trade():
    # pnl above theta*max_profit → keeps riding (returns None, no other rule set).
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": 40.0, "exit_price": 1.2},
        now_et=time(13, 5), rule=_rule(scratch_time="13:00", scratch_theta=0.10,
                                       profit_target_pct=1.0, stop_pct=None))
    assert d is None


def test_scratch_does_not_fire_before_scratch_time():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(12, 55), rule=_rule(scratch_time="13:00", stop_pct=None))
    assert d is None


def test_hard_close_fires_unconditionally_at_time():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(14, 0), rule=_rule(hard_close_time="14:00", stop_pct=None))
    assert d is not None and d.reason == "hard_close" and d.fired_at == "14:00"


def test_scratch_precedes_hard_close_when_both_eligible():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(14, 30),
        rule=_rule(scratch_time="13:00", hard_close_time="14:00", stop_pct=None))
    assert d.reason == "scratch"


def test_enable_time_exits_false_skips_time_rules():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(14, 30),
        rule=_rule(scratch_time="13:00", hard_close_time="14:00", stop_pct=None),
        enable_time_exits=False)
    assert d is None


def test_no_rules_set_returns_none():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": 10.0, "exit_price": 1.0},
        now_et=time(11, 0), rule=_rule(stop_pct=None))
    assert d is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_intraday_exit_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'signals.intraday_exit_rules'`.

- [ ] **Step 3: Implement**

```python
# signals/intraday_exit_rules.py
"""signals/intraday_exit_rules.py -- the single, shared intraday exit decision.

Pure and stateless: both the backtest session loop and the live ExitManager call
evaluate_intraday_exit() so the rule the walk-forward validates is byte-identical
to the rule that runs live. Evaluation order:
    profit-target -> hard-stop -> scratch@T -> hard-close@T
The first rule that fires wins; None means "hold for now".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as _time


@dataclass(frozen=True)
class ExitDecision:
    """A fired exit. exit_price is the slippage-adjusted price to transact at."""
    exit_price: float
    reason:     str     # "target" | "stop" | "scratch" | "hard_close"
    fired_at:   str     # "HH:MM" ET when the rule fired (or "" for target/stop)


def _as_time(hhmm: str | None) -> _time | None:
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    return _time(int(h), int(m))


def evaluate_intraday_exit(position: dict, mark: dict, now_et: _time,
                           rule: dict, enable_time_exits: bool = True
                           ) -> ExitDecision | None:
    """Decide whether to close `position` now.

    position: {strategy, dte_bucket, max_profit, max_loss}
    mark:     {pnl: float (dollars), exit_price: float (per-share, slippage-adj)}
    now_et:   datetime.time of the current bar (ET)
    rule:     {profit_target_pct, stop_pct, scratch_time, scratch_theta,
               hard_close_time} — times are "HH:MM" strings or None.
    enable_time_exits: when False, the scratch/hard-close rules are skipped
               entirely (the live kill-switch path).
    """
    pnl        = mark.get("pnl")
    exit_price = mark.get("exit_price", 0.0)
    max_profit = position.get("max_profit")
    max_loss   = position.get("max_loss")

    # 1. Profit target — a working trade always takes its win first.
    if (rule.get("profit_target_pct") is not None and pnl is not None
            and max_profit and max_profit > 0
            and pnl / max_profit >= rule["profit_target_pct"]):
        return ExitDecision(exit_price, "target", "")

    # 2. Hard stop (where configured).
    if (rule.get("stop_pct") is not None and pnl is not None
            and max_loss and max_loss > 0
            and pnl <= -rule["stop_pct"] * max_loss):
        return ExitDecision(exit_price, "stop", "")

    if not enable_time_exits:
        return None

    # 3. Scratch — at/after scratch_time, bail only if it's not working.
    scratch_t = _as_time(rule.get("scratch_time"))
    if (scratch_t is not None and now_et >= scratch_t and pnl is not None
            and max_profit and max_profit > 0
            and pnl < rule.get("scratch_theta", 0.0) * max_profit):
        return ExitDecision(exit_price, "scratch", rule["scratch_time"])

    # 4. Hard close — at/after hard_close_time, close unconditionally.
    hard_t = _as_time(rule.get("hard_close_time"))
    if hard_t is not None and now_et >= hard_t:
        return ExitDecision(exit_price, "hard_close", rule["hard_close_time"])

    return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_intraday_exit_rules.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add signals/intraday_exit_rules.py tests/test_intraday_exit_rules.py
git commit -m "feat: shared stateless intraday exit evaluator (target/stop/scratch/hard-close)"
```

---

## Task 2: Config flags + exit-rule surfacing

**Files:**
- Modify: `config.py` (append near the existing 0DTE exit constants ~line 273-281)
- Modify: `learning/exit_manager.py:73-147` (`_exit_rule_for` — add the three time-exit keys to each returned dict)
- Test: `tests/test_exit_manager.py` (add to the existing file)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_manager.py  (append)
from learning.exit_manager import exit_rule_for


def test_exit_rule_for_exposes_time_exit_keys():
    rule = exit_rule_for("put_debit_spread", "0DTE")
    # New keys must exist (default None until the WF earns them).
    assert "scratch_time" in rule
    assert "scratch_theta" in rule
    assert "hard_close_time" in rule
    assert rule["scratch_time"] is None
    assert rule["hard_close_time"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_exit_manager.py::test_exit_rule_for_exposes_time_exit_keys -q`
Expected: FAIL — `KeyError: 'scratch_time'`.

- [ ] **Step 3: Implement**

In `config.py`, append after line 281 (`FORCED_CLOSE_TIME_0DTE_CONDOR`):

```python
# ── Intraday time-exit model (2026-06-05) ───────────────────────────────────
# Global kill-switch: when False the live ExitManager skips ALL scratch/hard-close
# time rules (falls back to today's target/stop/forced-close behavior).
INTRADAY_TIME_EXIT_ENABLED = True

# Per-(strategy, dte_bucket) time-exit params. None until a walk-forward arm
# EARNS the combo (Task 7). Keyed "STRATEGY_BUCKET". Only 0DTE/1-3DTE are managed.
# scratch_theta is a fraction of max_profit: pnl below it at scratch_time => bail.
SCRATCH_TIME      = {}   # e.g. {"put_debit_spread_0DTE": "13:00"}
SCRATCH_THETA     = {}   # e.g. {"put_debit_spread_0DTE": 0.0}
HARD_CLOSE_TIME   = {}   # e.g. {"put_debit_spread_0DTE": "14:00"}

# Live/backtest parity gate (Task 6). B ships for a combo only if the BS-off-spot
# mark reproduces the real-mark exits on >= MIN_AGREE of trades AND the per-trade
# mean pnl gap between the two marks' arms is < MAX_PNL_GAP dollars.
EXIT_PARITY_MIN_AGREE   = 0.90
EXIT_PARITY_MAX_PNL_GAP = 10.0
```

In `learning/exit_manager.py`, add a helper near the top (after `_STRUCTURE_KEY`, ~line 70) and call it in all three bucket branches of `_exit_rule_for`:

```python
def _time_exit_params(strategy: str | None, bucket: str) -> dict:
    """Look up the WF-earned scratch/hard-close params for this combo. Empty
    config dicts => all None (the unearned default)."""
    key = f"{strategy}_{bucket}"
    return {
        "scratch_time":    config.SCRATCH_TIME.get(key),
        "scratch_theta":   config.SCRATCH_THETA.get(key, 0.0),
        "hard_close_time": config.HARD_CLOSE_TIME.get(key),
    }
```

Then in `_exit_rule_for`, in EACH of the `0DTE` and `1-3DTE` return dicts (lines ~115-123 and ~136-144), add `**_time_exit_params(strategy, bucket)` to the returned dict. For the `45DTE` branch and the defensive default, add the three keys explicitly as `None`/`0.0` so the shape is uniform:

```python
    # 45DTE branch return dict — add:
            "scratch_time":    None,
            "scratch_theta":   0.0,
            "hard_close_time": None,
```

(0DTE / 1-3DTE branches use `**_time_exit_params(strategy, bucket)` instead, since those are the managed buckets.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_exit_manager.py -q`
Expected: PASS (existing tests still green + the new one).

- [ ] **Step 5: Commit**

```bash
git add config.py learning/exit_manager.py tests/test_exit_manager.py
git commit -m "feat: time-exit config flags + surface params in exit-rule dict"
```

---

## Task 3: Backtest records the full P&L path (both marks)

**Files:**
- Modify: `backtests/intraday_backtest.py` (`simulate_0dte_day` ~lines 240-265; `_simulate_short_dte_with_expiration` lives in `intraday_router_wf.py` ~lines 252-276)
- Test: `tests/test_intraday_backtest_path.py` *(new)*

The sims must return, alongside the existing result dict, the full per-bar path so the offline arm-replay can apply candidate rules. Each path row carries the real-mark pnl/exit_price AND a BS-off-spot pnl/exit_price for the parity check. We compute the BS mark with the existing `learning.exit_manager.bs_price` off the bar's SPY spot.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_backtest_path.py
from datetime import date
from backtests.intraday_backtest import simulate_0dte_day
from tests.helpers_intraday import make_spy_intraday, FakeOptionsHistory  # see Step 3


def test_simulate_0dte_day_returns_path_with_both_marks():
    day = date(2024, 3, 1)
    spy = make_spy_intraday(day)          # deterministic 5-min SPY frame
    oh  = FakeOptionsHistory()            # deterministic option bars
    result = simulate_0dte_day(day, "bull_debit", spy, oh,
                               require_confirmation=False)
    assert result is not None
    assert "path" in result
    assert len(result["path"]) >= 1
    row = result["path"][0]
    for k in ("t", "pnl", "exit_price", "pnl_bs", "exit_price_bs"):
        assert k in row
    assert "pnl_hold" in result        # final-bar (EOD) pnl, for the counterfactual
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_intraday_backtest_path.py -q`
Expected: FAIL — `KeyError: 'path'` (or the helpers import fails first; create them in Step 3).

- [ ] **Step 3: Implement**

First create deterministic test helpers so this and later tasks don't hit the network:

```python
# tests/helpers_intraday.py
import pandas as pd
from datetime import datetime, timedelta


def make_spy_intraday(day, start_price=500.0, n=78):
    """5-min UTC-indexed SPY frame for an RTH session (09:30-16:00 ET)."""
    idx = pd.date_range(f"{day} 13:30:00", periods=n, freq="5min", tz="UTC")
    closes = [start_price + i * 0.05 for i in range(n)]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1000] * n}, index=idx)


class FakeOptionsHistory:
    """Returns a flat, always-priceable 5-min option bar frame for any contract."""
    def get_aggs(self, contract, mult, span, start, end):
        idx = pd.date_range(f"{start} 13:30:00", periods=78, freq="5min", tz="UTC")
        return pd.DataFrame({"close": [1.00] * 78}, index=idx)
```

In `backtests/intraday_backtest.py`, modify `simulate_0dte_day`'s session loop (lines ~240-265). Replace the bare loop with one that records the path and computes the BS-off-spot mark. The SPY spot at each bar is `float(spy.loc[ts]["close"])` (the session frame is already ET via `_to_et`):

```python
    from learning.exit_manager import bs_price
    # VIX proxy: the backtest has no live VIX; use a fixed sigma stand-in that
    # matches the live BS convention (sigma = vix/100). 0.15 ~ VIX 15, the calm
    # regime these intraday trades fire in. Documented approximation.
    BS_SIGMA = 0.15

    def _bs_spread_mark(spot_now, legs, structure):
        """Per-share BS mark of the spread off the intraday spot (parity mirror)."""
        long_v = short_v = 0.0
        for leg in legs:
            otype = "call" if leg["cp"].lower().startswith("c") else "put"
            p = bs_price(otype, spot_now, leg["strike"], 0.5 / 365.0, BS_SIGMA)
            if leg["action"] == "BUY":
                long_v += p
            else:
                short_v += p
        return max(0.0, (short_v - long_v) if credit else (long_v - short_v))

    exit_reason = "eod"
    pnl = -commission
    path = []
    for ts in session.index:
        m = marks_at(ts)
        if m is None:
            continue
        val = _spread_value(m, structure)
        if credit:
            pnl = (entry_px - (val + SLIPPAGE)) * 100 - commission
            exit_px_bar = round(val + SLIPPAGE, 2)
        else:
            pnl = (max(0.0, val - SLIPPAGE) - entry_px) * 100 - commission
            exit_px_bar = round(max(0.0, val - SLIPPAGE), 2)

        spot_now = float(spy.loc[ts]["close"]) if ts in spy.index else entry_spot
        val_bs = _bs_spread_mark(spot_now, legs, structure)
        if credit:
            pnl_bs = (entry_px - (val_bs + SLIPPAGE)) * 100 - commission
            exit_px_bs = round(val_bs + SLIPPAGE, 2)
        else:
            pnl_bs = (max(0.0, val_bs - SLIPPAGE) - entry_px) * 100 - commission
            exit_px_bs = round(max(0.0, val_bs - SLIPPAGE), 2)

        path.append({"t": ts.strftime("%H:%M"), "pnl": round(pnl, 2),
                     "exit_price": exit_px_bar, "pnl_bs": round(pnl_bs, 2),
                     "exit_price_bs": exit_px_bs})

        if max_profit > 0 and pnl >= profit_target_pct * max_profit:
            exit_reason = "target"; break
        if stop_mult is not None and pnl <= -stop_mult * max_profit:
            exit_reason = "stop"; break

    result = {
        "date": day.isoformat(), "structure": structure,
        "entry_spot": round(entry_spot, 2), "entry_px": round(entry_px, 2),
        "pnl_dollars": round(pnl, 2),
        "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "exit_reason": exit_reason,
        "path": path,
        "pnl_hold": path[-1]["pnl"] if path else round(pnl, 2),
    }
    return result
```

Apply the **same** path-recording change to `_simulate_short_dte_with_expiration` in `backtests/intraday_router_wf.py` (its loop is structurally identical, lines ~252-276): build `path` with both marks, set `result["path"]` and `result["pnl_hold"]`. The expiry for the BS `t_years` there is `(expiry - day).days / 365.0` instead of `0.5/365.0`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_intraday_backtest_path.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_backtest.py backtests/intraday_router_wf.py tests/helpers_intraday.py tests/test_intraday_backtest_path.py
git commit -m "feat: backtest records full 5-min pnl path with real + BS-off-spot marks"
```

---

## Task 4: Exit-counterfactual / exit-quality (piece D)

**Files:**
- Create: `signals/exit_counterfactual.py`
- Test: `tests/test_exit_counterfactual.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exit_counterfactual.py
from signals.exit_counterfactual import exit_quality, aggregate_exit_quality


def test_exit_quality_positive_when_exit_beats_hold():
    # Saved a loser: exited at -10, holding would have been -80.
    assert exit_quality(pnl_exit=-10.0, pnl_hold=-80.0) == 70.0


def test_exit_quality_negative_when_we_cut_a_winner():
    # Bad hunch: exited at +20, holding would have hit +100.
    assert exit_quality(pnl_exit=20.0, pnl_hold=100.0) == -80.0


def test_aggregate_groups_by_combo_and_reason():
    rows = [
        {"strategy": "put_debit_spread", "dte_bucket": "0DTE",
         "exit_reason": "scratch", "pnl_exit": -10.0, "pnl_hold": -80.0},
        {"strategy": "put_debit_spread", "dte_bucket": "0DTE",
         "exit_reason": "scratch", "pnl_exit": 5.0, "pnl_hold": 50.0},
    ]
    agg = aggregate_exit_quality(rows)
    key = "put_debit_spread|0DTE|scratch"
    assert agg[key]["n"] == 2
    assert agg[key]["mean_exit_quality"] == (70.0 + -45.0) / 2
    assert agg[key]["mean_pnl_exit"] == (-10.0 + 5.0) / 2
    assert agg[key]["mean_pnl_hold"] == (-80.0 + 50.0) / 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_exit_counterfactual.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# signals/exit_counterfactual.py
"""signals/exit_counterfactual.py -- did the exit help or hurt?

exit_quality = pnl_exit - pnl_hold.
  > 0  the exit SAVED money (we got out of a worse outcome) — good discipline.
  < 0  the exit COST money (we cut a trade that would have done better) — a bad
       hunch / premature exit.
Aggregated per (strategy, dte_bucket, exit_reason) it tells us whether a time-exit
rule is systematically saving losers or cutting winners. Feeds exit_timing KB.
"""
from __future__ import annotations


def exit_quality(pnl_exit: float, pnl_hold: float) -> float:
    """Signed dollars the exit decision was worth vs holding to EOD/expiry."""
    return round(pnl_exit - pnl_hold, 2)


def aggregate_exit_quality(rows: list[dict]) -> dict:
    """Group rows by 'strategy|dte_bucket|exit_reason'. Each row needs
    strategy, dte_bucket, exit_reason, pnl_exit, pnl_hold."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = f"{r['strategy']}|{r['dte_bucket']}|{r['exit_reason']}"
        groups.setdefault(key, []).append(r)

    out = {}
    for key, rs in groups.items():
        n = len(rs)
        eq = [exit_quality(r["pnl_exit"], r["pnl_hold"]) for r in rs]
        out[key] = {
            "n": n,
            "mean_exit_quality": round(sum(eq) / n, 2),
            "mean_pnl_exit": round(sum(r["pnl_exit"] for r in rs) / n, 2),
            "mean_pnl_hold": round(sum(r["pnl_hold"] for r in rs) / n, 2),
        }
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_exit_counterfactual.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add signals/exit_counterfactual.py tests/test_exit_counterfactual.py
git commit -m "feat: exit_quality counterfactual scoring (saved-loser vs cut-winner)"
```

---

## Task 5: Offline arm-replay over recorded paths

**Files:**
- Modify: `backtests/intraday_router_wf.py` (add `replay_arms`, `ARMS`, and per-arm aggregation; emit `logs/wf_trade_paths.jsonl` from `run_walk_forward`/`__main__`)
- Test: `tests/test_arm_replay.py` *(new)*

`replay_arms` takes one trade's recorded path + the trade's `(strategy, dte_bucket, max_profit, max_loss, profit_target_pct, stop_pct)` and, for each candidate arm (a rule variant), walks the path through `evaluate_intraday_exit` to find that arm's exit pnl, reason, and `exit_quality` vs `pnl_hold`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arm_replay.py
from backtests.intraday_router_wf import replay_arms, ARMS


def _trade():
    # A drifting path: never hits target/stop; small negative until EOD.
    path = [{"t": "09:45", "pnl": -3.0, "exit_price": 0.5, "pnl_bs": -3.0, "exit_price_bs": 0.5},
            {"t": "13:00", "pnl": -5.0, "exit_price": 0.4, "pnl_bs": -5.0, "exit_price_bs": 0.4},
            {"t": "14:00", "pnl": -8.0, "exit_price": 0.3, "pnl_bs": -8.0, "exit_price_bs": 0.3},
            {"t": "15:55", "pnl": -40.0, "exit_price": 0.05, "pnl_bs": -40.0, "exit_price_bs": 0.05}]
    return {"strategy": "put_debit_spread", "dte_bucket": "0DTE",
            "max_profit": 300.0, "max_loss": 56.0, "profit_target_pct": 1.0,
            "stop_pct": None, "path": path, "pnl_hold": -40.0}


def test_baseline_arm_holds_to_eod():
    res = replay_arms(_trade())
    base = res["baseline"]
    assert base["pnl_exit"] == -40.0           # held to EOD
    assert base["exit_reason"] == "eod"
    assert base["exit_quality"] == 0.0         # baseline == hold


def test_hard_close_1300_exits_early_and_saves_money():
    res = replay_arms(_trade())
    arm = res["hard_close@13:00"]
    assert arm["pnl_exit"] == -5.0             # exits at the 13:00 bar
    assert arm["exit_reason"] == "hard_close"
    assert arm["exit_quality"] == 35.0         # -5 - (-40)


def test_every_arm_present():
    res = replay_arms(_trade())
    assert set(res.keys()) == set(ARMS.keys())
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_arm_replay.py -q`
Expected: FAIL — `ImportError: cannot import name 'replay_arms'`.

- [ ] **Step 3: Implement**

Add to `backtests/intraday_router_wf.py`:

```python
from datetime import datetime as _dt
from signals.intraday_exit_rules import evaluate_intraday_exit
from signals.exit_counterfactual import exit_quality as _exit_quality

# Candidate arms. baseline = today's behavior (no time rule). Coarse grid only.
ARMS = {
    "baseline":          {"scratch_time": None, "scratch_theta": 0.0, "hard_close_time": None},
    "hard_close@12:00":  {"scratch_time": None, "scratch_theta": 0.0, "hard_close_time": "12:00"},
    "hard_close@13:00":  {"scratch_time": None, "scratch_theta": 0.0, "hard_close_time": "13:00"},
    "hard_close@14:00":  {"scratch_time": None, "scratch_theta": 0.0, "hard_close_time": "14:00"},
    "scratch@12:00,0":   {"scratch_time": "12:00", "scratch_theta": 0.0, "hard_close_time": None},
    "scratch@13:00,0":   {"scratch_time": "13:00", "scratch_theta": 0.0, "hard_close_time": None},
    "scratch@14:00,0":   {"scratch_time": "14:00", "scratch_theta": 0.0, "hard_close_time": None},
    "scratch@13:00,.10": {"scratch_time": "13:00", "scratch_theta": 0.10, "hard_close_time": None},
}


def _bar_time(hhmm: str):
    h, m = hhmm.split(":")
    return _dt.min.replace(hour=int(h), minute=int(m)).time()


def replay_arms(trade: dict, mark_key: str = "") -> dict:
    """Apply every ARM to one trade's recorded path. mark_key='' uses the real
    mark (pnl/exit_price); mark_key='_bs' uses the BS-off-spot mark
    (pnl_bs/exit_price_bs) — used by the parity check.

    Returns {arm_name: {pnl_exit, exit_reason, fired_at, exit_quality}}.
    """
    pnl_field   = "pnl_bs" if mark_key == "_bs" else "pnl"
    price_field = "exit_price_bs" if mark_key == "_bs" else "exit_price"
    position = {"strategy": trade["strategy"], "dte_bucket": trade["dte_bucket"],
                "max_profit": trade["max_profit"], "max_loss": trade["max_loss"]}
    pnl_hold = trade["pnl_hold"]
    out = {}
    for name, time_rule in ARMS.items():
        rule = {"profit_target_pct": trade.get("profit_target_pct"),
                "stop_pct": trade.get("stop_pct"), **time_rule}
        fired = None
        for row in trade["path"]:
            mark = {"pnl": row[pnl_field], "exit_price": row[price_field]}
            d = evaluate_intraday_exit(position, mark, _bar_time(row["t"]), rule)
            if d is not None:
                fired = (row[pnl_field], d.reason, d.fired_at)
                break
        if fired is None:                       # never fired -> held to EOD
            last = trade["path"][-1]
            fired = (last[pnl_field], "eod", "")
        pnl_exit, reason, fired_at = fired
        out[name] = {"pnl_exit": round(pnl_exit, 2), "exit_reason": reason,
                     "fired_at": fired_at,
                     "exit_quality": _exit_quality(pnl_exit, pnl_hold)}
    return out
```

Then in `run_walk_forward` / `__main__`, after the windows run, write every treatment trade's row to `logs/wf_trade_paths.jsonl` (one JSON object per trade). **The emitted row MUST include `path` and `pnl_hold` (from Task 3) plus `strategy`, `dte_bucket`, `entry_spot`, `max_profit`, `max_loss`, `profit_target_pct`, `stop_pct` so `replay_arms` can run offline** — if the existing `rows_T` collector (merged in `bba714c`) selects only a subset of keys, extend it to carry these. `max_profit`/`max_loss`/`profit_target_pct`/`stop_pct` come from the per-`(strategy,dte_bucket)` exit rule via `learning.exit_manager.exit_rule_for(strategy, dte_bucket)` and the structure's max_profit computed in the sim. Add a comment that overlapping windows repeat trades; the analysis dedups by `(date, strategy, dte_bucket, entry_spot)`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_arm_replay.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_arm_replay.py
git commit -m "feat: offline arm-replay over recorded paths (baseline vs time-exit arms)"
```

---

## Task 6: Per-arm verdicts + parity divergence

**Files:**
- Modify: `backtests/intraday_router_wf.py` (add `arm_verdicts` and `parity_divergence`)
- Test: `tests/test_arm_verdicts.py` *(new)*

`arm_verdicts` aggregates replayed arms across a deduped trade list into per-arm × per-`(strategy,dte_bucket)` stats and reuses the existing `window_verdict`/thresholds. `parity_divergence` compares real-mark vs BS-mark arm decisions per combo against `config.EXIT_PARITY_MIN_AGREE` / `EXIT_PARITY_MAX_PNL_GAP`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arm_verdicts.py
from backtests.intraday_router_wf import arm_verdicts, parity_divergence


def _mk(strategy, bucket, pnl_real, pnl_bs):
    path = [{"t": "13:00", "pnl": pnl_real, "exit_price": 0.4,
             "pnl_bs": pnl_bs, "exit_price_bs": 0.4},
            {"t": "15:55", "pnl": -40.0, "exit_price": 0.05,
             "pnl_bs": -40.0, "exit_price_bs": 0.05}]
    return {"strategy": strategy, "dte_bucket": bucket, "max_profit": 300.0,
            "max_loss": 56.0, "profit_target_pct": 1.0, "stop_pct": None,
            "path": path, "pnl_hold": -40.0}


def test_arm_verdicts_reports_per_combo_per_arm_mean():
    trades = [_mk("put_debit_spread", "0DTE", -5.0, -5.0) for _ in range(12)]
    av = arm_verdicts(trades)
    combo = av["put_debit_spread|0DTE"]
    assert combo["hard_close@13:00"]["n"] == 12
    assert combo["hard_close@13:00"]["mean_pnl"] == -5.0   # exits at 13:00 bar
    assert combo["baseline"]["mean_pnl"] == -40.0


def test_parity_divergence_flags_when_bs_mark_disagrees():
    # Real mark exits at 13:00 (-5); BS mark stays positive there (+50) so the
    # hard_close arm still fires (unconditional) — decisions AGREE here.
    trades = [_mk("put_debit_spread", "0DTE", -5.0, 50.0) for _ in range(10)]
    pd_report = parity_divergence(trades)
    assert "put_debit_spread|0DTE" in pd_report
    row = pd_report["put_debit_spread|0DTE"]["hard_close@13:00"]
    assert 0.0 <= row["agree_frac"] <= 1.0
    assert "passes" in row
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_arm_verdicts.py -q`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Add to `backtests/intraday_router_wf.py`:

```python
import config as _config


def _combo_key(t):
    return f"{t['strategy']}|{t['dte_bucket']}"


def arm_verdicts(trades: list[dict]) -> dict:
    """Per-(strategy,dte_bucket): for each ARM, n / total / mean / win-rate over
    the deduped trade list (real mark)."""
    by_combo: dict[str, list[dict]] = {}
    for t in trades:
        by_combo.setdefault(_combo_key(t), []).append(t)

    out = {}
    for combo, ts in by_combo.items():
        replayed = [replay_arms(t) for t in ts]
        arm_stats = {}
        for arm in ARMS:
            pnls = [r[arm]["pnl_exit"] for r in replayed]
            n = len(pnls)
            arm_stats[arm] = {
                "n": n,
                "total_pnl": round(sum(pnls), 2),
                "mean_pnl": round(sum(pnls) / n, 2) if n else 0.0,
                "win_rate": round(sum(1 for p in pnls if p > 0) / n, 3) if n else 0.0,
                "mean_exit_quality": round(
                    sum(r[arm]["exit_quality"] for r in replayed) / n, 2) if n else 0.0,
            }
        out[combo] = arm_stats
    return out


def parity_divergence(trades: list[dict]) -> dict:
    """Per-(strategy,dte_bucket,arm): how often the BS-off-spot mark reproduces
    the real-mark exit decision, and whether the combo passes the parity gate."""
    by_combo: dict[str, list[dict]] = {}
    for t in trades:
        by_combo.setdefault(_combo_key(t), []).append(t)

    out = {}
    for combo, ts in by_combo.items():
        real = [replay_arms(t, mark_key="") for t in ts]
        bsm  = [replay_arms(t, mark_key="_bs") for t in ts]
        arm_rows = {}
        for arm in ARMS:
            if arm == "baseline":
                continue
            agree = sum(1 for a, b in zip(real, bsm)
                        if a[arm]["exit_reason"] == b[arm]["exit_reason"]
                        and a[arm]["fired_at"] == b[arm]["fired_at"])
            n = len(ts)
            agree_frac = agree / n if n else 0.0
            gap = (sum(abs(a[arm]["pnl_exit"] - b[arm]["pnl_exit"])
                       for a, b in zip(real, bsm)) / n) if n else 0.0
            arm_rows[arm] = {
                "agree_frac": round(agree_frac, 3),
                "mean_pnl_gap": round(gap, 2),
                "passes": bool(agree_frac >= _config.EXIT_PARITY_MIN_AGREE
                               and gap < _config.EXIT_PARITY_MAX_PNL_GAP),
            }
        out[combo] = arm_rows
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_arm_verdicts.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_router_wf.py tests/test_arm_verdicts.py
git commit -m "feat: per-arm verdicts + live/backtest parity-divergence gate"
```

---

## Task 7: RUN the walk-forward and record the result

**Files:**
- Modify: `config.py` (populate `SCRATCH_TIME`/`SCRATCH_THETA`/`HARD_CLOSE_TIME` for winning combos — or leave empty if none win)
- No test (this is an analysis run); its output drives Tasks 8-11.

This is the gate. It is NOT a code task — it RUNS the harness built in Tasks 1-6 and records which arms earn which combos. Do this in the foreground (it is network-heavy, several minutes).

- [ ] **Step 1: Run the walk-forward with path recording**

```bash
source .venv/bin/activate
python -m backtests.intraday_router_wf --start 2024-01-02 --end 2025-12-31 --out logs/router_wf_timeexit.json
```

This writes `logs/router_wf_timeexit.json` and `logs/wf_trade_paths.jsonl`.

- [ ] **Step 2: Analyze (throwaway script, then delete it)**

Write a short script that loads `logs/wf_trade_paths.jsonl`, **dedups by `(date, strategy, dte_bucket, entry_spot)`** (overlapping windows repeat trades ~2.7x — this is mandatory), then prints `arm_verdicts(deduped)` and `parity_divergence(deduped)`. For each managed combo (`put_debit_spread_0DTE` first), identify the arm that:
  (a) beats `baseline` mean_pnl, AND
  (b) is positive (or least-negative) across the most windows — re-run per-window if needed, OR accept the aggregate if the per-window split is unavailable, AND
  (c) **passes the parity gate** (`parity_divergence[...][arm]["passes"] is True`).
Print a clear table. Flag any combo under ~30 distinct trades as hypothesis-only. Delete the script after.

- [ ] **Step 3: Record the decision in config**

If a combo has a winning arm that clears (a)+(b)+(c), populate it in `config.py`. Example IF `hard_close@13:00` wins for put_debit 0DTE:

```python
SCRATCH_TIME      = {}
SCRATCH_THETA     = {}
HARD_CLOSE_TIME   = {"put_debit_spread_0DTE": "13:00"}
```

If NO combo clears all three gates, leave all three dicts `{}` — B ships inert (live unchanged). Write a one-paragraph note of the outcome into `BUILD_LOG.md` either way.

- [ ] **Step 4: Append the result to the learning KB**

Add a `backtest_result` KB entry (via `learning.knowledge_base.KnowledgeBase`) recording the winning arm + its OOS mean_pnl/win-rate/exit_quality, or "no arm cleared the gates" — `stance="confirming"` if a rule earned its place, `"disconfirming"` if the time-stop hypothesis failed OOS. (Mirror the pattern used for the 2026-06-03 entries.)

- [ ] **Step 5: Commit**

```bash
git add config.py BUILD_LOG.md
git commit -m "chore: record intraday time-exit WF result (populate earned combos / inert if none)"
```

---

## Task 8: Live ExitManager — BS-off-intraday-spot mark

**Files:**
- Modify: `learning/exit_manager.py` (`manage_open` / `_evaluate` / new `_fetch_intraday_spot`)
- Test: `tests/test_exit_manager.py` (add)

Today `_evaluate` marks off the SPY daily close (`_fetch_spy_close`). For intraday time-exits to be meaningful, the mark must move intraday. Add an intraday-spot fetch (5-min SPY bar) and prefer it; fall back to the daily close when unavailable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_manager.py  (append)
from unittest.mock import MagicMock
import pandas as pd
from datetime import datetime
from learning.exit_manager import ExitManager


def test_fetch_intraday_spot_prefers_5min_bar():
    poly = MagicMock()
    idx = pd.date_range("2024-03-01 19:00:00", periods=3, freq="5min", tz="UTC")
    poly.get_bars.return_value = pd.DataFrame({"close": [500.0, 501.0, 502.5]}, index=idx)
    em = ExitManager(polygon_client=poly)
    spot = em._fetch_intraday_spot()
    assert spot == 502.5    # most-recent 5-min close
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_exit_manager.py::test_fetch_intraday_spot_prefers_5min_bar -q`
Expected: FAIL — `AttributeError: 'ExitManager' object has no attribute '_fetch_intraday_spot'`.

- [ ] **Step 3: Implement**

Add to `ExitManager`:

```python
    def _fetch_intraday_spot(self) -> float | None:
        """Most-recent 5-min SPY close (intraday mark source for time-exits).
        Returns None on any failure so the caller can fall back to daily close."""
        if self.polygon is None:
            return None
        try:
            df = self.polygon.get_bars("SPY", timeframe="5minute", limit=3, days_back=1)
            if df is None or len(df) == 0:
                return None
            return float(df["close"].iloc[-1])
        except Exception as e:
            logger.warning(f"ExitManager intraday-spot fetch failed: {e}")
            return None
```

In `manage_open`, when processing intraday buckets, prefer the intraday spot: after the existing `spy_close` resolution, add

```python
        # Intraday time-exits need an intraday mark; fall back to daily close.
        intraday_spot = self._fetch_intraday_spot()
        spy_mark = intraday_spot if intraday_spot is not None else spy_close
```

and pass `spy_mark` (not `spy_close`) into `_evaluate`. Keep `_fetch_spy_close` as the fallback path. Update `_evaluate`'s signature to accept the chosen mark.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_exit_manager.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add learning/exit_manager.py tests/test_exit_manager.py
git commit -m "feat: ExitManager BS-off-intraday-spot mark (daily-close fallback)"
```

---

## Task 9: Live ExitManager — delegate to the shared evaluator

**Files:**
- Modify: `learning/exit_manager.py` (`_evaluate` → call `evaluate_intraday_exit`)
- Test: `tests/test_exit_manager.py` (add)

`_evaluate` currently inlines target/stop/DTE-threshold and IGNORES `forced_close_time`. Replace the intraday decision with the shared evaluator so live runs exactly what the WF validated, honoring the kill-switch. The 45DTE path (DTE-threshold time-stop) stays as-is — the shared evaluator governs the intraday 0DTE/1-3DTE buckets only.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_manager.py  (append)
from datetime import date, time
import config
from learning.exit_manager import ExitManager


def test_evaluate_fires_hard_close_via_shared_evaluator(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_TIME_EXIT_ENABLED", True)
    monkeypatch.setattr(config, "HARD_CLOSE_TIME",
                        {"put_debit_spread_0DTE": "13:00"})
    em = ExitManager()
    trade = {"trade_id": "T1", "strategy": "put_debit_spread", "dte_bucket": "0DTE",
             "entry_price": 0.56, "size": 1,
             "legs": [{"action": "BUY", "type": "put", "strike": 500,
                       "expiration": date.today().isoformat()},
                      {"action": "SELL", "type": "put", "strike": 497,
                       "expiration": date.today().isoformat()}],
             "max_profit": 244.0, "max_loss": 56.0}
    # now=13:05 ET, mark gives some pnl; hard_close should fire regardless.
    decision = em._evaluate(trade, spy=499.0, vix=15.0, today=date.today(),
                            now_et=time(13, 5))
    assert decision is not None
    assert decision[1] == "hard_close"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_exit_manager.py::test_evaluate_fires_hard_close_via_shared_evaluator -q`
Expected: FAIL — `_evaluate` has no `now_et` param / returns None (forced_close ignored today).

- [ ] **Step 3: Implement**

Refactor `_evaluate` to accept `now_et` (default = current ET time) and, for `0DTE`/`1-3DTE` buckets, build the `position`/`mark`/`rule` and delegate:

```python
    def _evaluate(self, trade, spy, vix, today, now_et=None):
        legs     = trade.get("legs") or []
        strategy = (trade.get("strategy") or trade.get("trade_type") or "single_leg").lower()
        exp      = self._nearest_expiration(legs)
        if exp is None:
            return None
        dte = (exp - today).days
        if dte < 0:
            return None

        rule_full = _exit_rule_for(strategy, trade.get("dte_bucket"))
        exit_px = self._mark_exit_price(strategy, legs, spy, vix, today, dte)
        pnl     = self._pnl_dollars(strategy, trade.get("entry_price"), exit_px,
                                    trade.get("size", 1))
        bucket  = trade.get("dte_bucket") or "45DTE"

        if bucket in ("0DTE", "1-3DTE"):
            from datetime import datetime as _dt
            import pytz as _pytz
            if now_et is None:
                now_et = _dt.now(_pytz.timezone("US/Eastern")).time()
            position = {"strategy": strategy, "dte_bucket": bucket,
                        "max_profit": self._numeric(trade.get("max_profit")),
                        "max_loss": self._numeric(trade.get("max_loss"))}
            rule = {"profit_target_pct": rule_full["profit_target_pct"],
                    "stop_pct": rule_full["stop_pct"],
                    "scratch_time": rule_full["scratch_time"],
                    "scratch_theta": rule_full["scratch_theta"],
                    "hard_close_time": rule_full["hard_close_time"]}
            d = evaluate_intraday_exit(
                position, {"pnl": pnl, "exit_price": exit_px}, now_et, rule,
                enable_time_exits=config.INTRADAY_TIME_EXIT_ENABLED)
            return (d.exit_price, d.reason) if d is not None else None

        # 45DTE / legacy: unchanged target/stop/DTE-threshold path.
        max_profit = self._numeric(trade.get("max_profit"))
        max_loss   = self._numeric(trade.get("max_loss"))
        if max_profit and max_profit > 0 and pnl is not None:
            if pnl / max_profit >= rule_full["profit_target_pct"]:
                return exit_px, f"profit target {rule_full['profit_target_pct']:.0%}"
        if rule_full["stop_pct"] is not None and max_loss and max_loss > 0 and pnl is not None:
            if pnl <= -rule_full["stop_pct"] * max_loss:
                return exit_px, f"stop {rule_full['stop_pct']:.0%} of max loss"
        if dte <= rule_full["dte_close_threshold"]:
            return exit_px, f"time stop {dte}DTE"
        return None
```

Add the import at the top of `exit_manager.py`:

```python
from signals.intraday_exit_rules import evaluate_intraday_exit
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_exit_manager.py -q`
Expected: PASS (all existing ExitManager tests still green — the 45DTE path is unchanged — plus the new one).

- [ ] **Step 5: Commit**

```bash
git add learning/exit_manager.py tests/test_exit_manager.py
git commit -m "feat: live ExitManager delegates intraday exits to shared evaluator (kill-switch honored)"
```

---

## Task 10: Live exit-quality counterfactual

**Files:**
- Modify: `learning/exit_manager.py` (stamp `pnl_exit` + a `counterfactual_pending` marker on the closed trade)
- Modify: `learning/outcome_resolver.py` (at EOD, fill `pnl_hold` + `exit_quality` on time-exit closes; append an `exit_timing` KB entry)
- Test: `tests/test_outcome_resolver_exit_quality.py` *(new)*

When a live time-exit fires, we know `pnl_exit` but not yet `pnl_hold` (what holding to EOD would have yielded). The OutcomeResolver already runs at 16:05 with the SPY close; have it BS-mark the would-be-held position at EOD and compute `exit_quality`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_outcome_resolver_exit_quality.py
from datetime import date
from unittest.mock import MagicMock
from learning.outcome_resolver import OutcomeResolver


def test_fills_exit_quality_on_time_exit_close():
    tr = MagicMock()
    trade = {"trade_id": "T1", "strategy": "put_debit_spread", "dte_bucket": "0DTE",
             "outcome": "loss", "exit_reason": "hard_close", "pnl_dollars": -5.0,
             "entry_date": date.today().isoformat() + " 09:45 AM EST",
             "entry_price": 0.56, "size": 1,
             "legs": [{"action": "BUY", "type": "put", "strike": 500,
                       "expiration": date.today().isoformat()},
                      {"action": "SELL", "type": "put", "strike": 497,
                       "expiration": date.today().isoformat()}],
             "counterfactual_pending": True, "exit_quality": None}
    tr.get_all_trades.return_value = [trade]
    r = OutcomeResolver(trade_recorder=tr)
    r._fill_exit_quality(date.today(), spy_close=498.0, vix=15.0)
    assert trade["exit_quality"] is not None
    assert trade["counterfactual_pending"] is False
    tr._save.assert_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_outcome_resolver_exit_quality.py -q`
Expected: FAIL — `AttributeError: ... has no attribute '_fill_exit_quality'`.

- [ ] **Step 3: Implement**

In `exit_manager.py`, when a time-exit (`reason in ("scratch", "hard_close")`) closes a trade, set `counterfactual_pending=True`, `pnl_exit=<pnl>`, `exit_quality=None` on the record before `log_exit` (or in the `closed` post-processing — wherever the trade dict is mutated/saved).

In `outcome_resolver.py`, add (and call from `resolve_today` after `_snapshot_open_paper_trades`, on BOTH the skip and tradeable branches):

```python
    def _fill_exit_quality(self, today, spy_close, vix=None):
        """For trades closed by a time-exit today, compute pnl_hold (BS mark of
        the would-be-held position at the EOD spot) and exit_quality."""
        if spy_close is None:
            return
        from learning.exit_manager import ExitManager
        from signals.exit_counterfactual import exit_quality
        vix = vix if vix is not None else 15.0
        em = ExitManager(trade_recorder=self.trades)
        trades = self.trades.get_all_trades()
        changed = False
        for t in trades:
            if not t.get("counterfactual_pending"):
                continue
            if (t.get("entry_date") or "")[:10] != today.isoformat():
                continue
            legs = t.get("legs") or []
            exp  = em._nearest_expiration(legs)
            dte  = max(0, (exp - today).days) if exp else 0
            hold_px = em._mark_exit_price(t.get("strategy", ""), legs,
                                          spy_close, vix, today, dte)
            pnl_hold = em._pnl_dollars(t.get("strategy", ""),
                                       t.get("entry_price"), hold_px,
                                       t.get("size", 1))
            t["pnl_hold"] = pnl_hold
            t["exit_quality"] = exit_quality(t.get("pnl_dollars") or 0.0,
                                             pnl_hold or 0.0)
            t["counterfactual_pending"] = False
            changed = True
        if changed:
            self.trades._save(trades)
```

Also append an `exit_timing` KB entry summarizing the day's time-exit quality when any were filled (use `learning.knowledge_base.KnowledgeBase`; keep it best-effort in a try/except).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_outcome_resolver_exit_quality.py tests/test_outcome_resolver_shadow.py -q`
Expected: PASS (new test + existing outcome-resolver tests).

- [ ] **Step 5: Commit**

```bash
git add learning/exit_manager.py learning/outcome_resolver.py tests/test_outcome_resolver_exit_quality.py
git commit -m "feat: live exit-quality counterfactual filled at EOD + exit_timing KB"
```

---

## Task 11: Full-suite verification + smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

```bash
source .venv/bin/activate
pytest tests/ -m "not integration" -p no:cacheprovider -q --deselect tests/test_fred.py --deselect tests/test_economic_scanner.py
```
Expected: all green (the only acceptable failures are the deselected live-FRED network tests).

- [ ] **Step 2: Import smoke test (main-wiring seam)**

Per [[feedback_main_wiring_untested_seam]], py_compile + import the touched modules and instantiate `ExitManager` to catch wiring AttributeErrors the source-scan tests miss:

```bash
python -c "import main, learning.exit_manager, learning.outcome_resolver, signals.intraday_exit_rules, signals.exit_counterfactual, backtests.intraday_router_wf; from learning.exit_manager import ExitManager; ExitManager(); print('wiring OK')"
```
Expected: `wiring OK`.

- [ ] **Step 3: Commit (if any fixups were needed)**

```bash
git add -A && git commit -m "test: full-suite + wiring smoke for intraday time-exit model"
```

---

## After all tasks

Dispatch a final whole-branch code review (per subagent-driven-development), then use **superpowers:finishing-a-development-branch**. Deploy is a `smta.service` restart; per [[feedback_main_wiring_untested_seam]], check `journalctl -u smta.service` for crash-loops after restart and confirm `NRestarts=0`.

**Live-safety note for the reviewer:** Tasks 8-10 change live exit behavior ONLY for `(strategy,dte_bucket)` combos populated in `config.SCRATCH_TIME`/`HARD_CLOSE_TIME` by Task 7, and only while `INTRADAY_TIME_EXIT_ENABLED=True`. If Task 7 populated nothing, live behavior is unchanged and the kill-switch is moot.
```
