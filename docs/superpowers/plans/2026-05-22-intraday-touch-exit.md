# Intraday-Touch Exit Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `intraday_touch` mode to the realistic pricer (re-marks the spread at the day's HIGH / LOW / CLOSE, exits at the first mark that hits the profit target) and a walk-forward comparison script that evaluates the measured improvement against six ship-bar presets.

**Architecture:** One new keyword parameter on the existing `simulate_trade` function (default off, preserves every existing caller). One new standalone script `backtests/intraday_touch_wf.py` that runs the realistic pricer twice on identical entry days, computes per-trade Δ metrics + attribution + per-regime breakdown, and prints a verdict matrix against six named presets. The `default-2σ` preset is the auto-ship pin; the others are learning context.

**Tech Stack:** Python, pandas, numpy. Reuses `backtests/realistic_pricing.py`, `backtests/spy_daily_backtest.py`.

**Spec:** `docs/superpowers/specs/2026-05-22-intraday-touch-exit-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `config.py` | Modify | Add 3 constants for the binding ship-bar floors. |
| `backtests/realistic_pricing.py` | Modify | Add `intraday_touch: bool = False` param + 3-mark logic in the walk loop. |
| `backtests/intraday_touch_wf.py` | Create | Walk-forward harness — runs touch off vs on, aggregates metrics, prints verdict matrix. |
| `tests/test_realistic_pricing.py` | Extend | Three new tests for `intraday_touch` behavior. |
| `tests/test_intraday_touch_wf.py` | Create | Unit tests for metrics aggregation + preset verdict logic. |
| `BUILD_LOG.md` | Append | Verdict entry after Task 5 runs the harness. |

---

## Task 1: Config — ship-bar floor constants

**Files:**
- Modify: `config.py` (add a section after the existing `META-LABELING` block)
- Test: `tests/test_intraday_touch_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_touch_config.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_intraday_touch_ship_bar_constants_present():
    # These are the three binding floors for the default-2σ preset in the
    # walk-forward harness. Other presets are hard-coded in the harness itself.
    assert config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR == 25.0
    assert config.INTRADAY_TOUCH_SHIP_MIN_FRAC   == 0.10
    assert config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB == 0.15
```

- [ ] **Step 2: Run test, verify it FAILS**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_touch_config.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'INTRADAY_TOUCH_SHIP_MIN_DOLLAR'`

- [ ] **Step 3: Add the constants to `config.py`**

Find the `META-LABELING` block (the section that starts with the comment line containing `# META-LABELING` and ends with the line `META_MODEL_PATH = "logs/learning/meta_model.joblib"`). Immediately AFTER that block, add:

```python


# ─────────────────────────────────────────
# INTRADAY-TOUCH EXIT (backtest ship-bar floors)
# ─────────────────────────────────────────
# Binding floors for the default-2σ preset in backtests/intraday_touch_wf.py.
# Five other presets are hard-coded inside the harness itself (learning context;
# they print verdicts but do not auto-ship). See spec
# docs/superpowers/specs/2026-05-22-intraday-touch-exit-design.md §6.
INTRADAY_TOUCH_SHIP_MIN_DOLLAR = 25.0    # statistical floor ($/trade, ~2σ on ~230 OOS)
INTRADAY_TOUCH_SHIP_MIN_FRAC   = 0.10    # scale floor (improvement >= 10% of baseline)
INTRADAY_TOUCH_SHIP_MIN_ATTRIB = 0.15    # >=15% of OOS exits via target_intraday
```

- [ ] **Step 4: Run test, verify it PASSES**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_touch_config.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_intraday_touch_config.py
git commit -m "feat: ship-bar floor constants for intraday-touch exit backtest"
```

---

## Task 2: `simulate_trade` — add `intraday_touch` parameter

**Files:**
- Modify: `backtests/realistic_pricing.py` (function `simulate_trade`, lines ~116-194)
- Test: `tests/test_realistic_pricing.py` (extend existing file)

The change adds an optional `intraday_touch: bool = False` parameter. When True, the walk loop re-marks the spread at the day's HIGH and LOW in addition to the CLOSE, and exits with `exit_reason="target_intraday"` if the best intraday mark hits the profit target on a day where the close mark did not. Default off = byte-identical to today.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_realistic_pricing.py`:

```python
from datetime import date, timedelta


def _intraday_df(n: int, closes: list[float], highs: list[float], lows: list[float]):
    """Build an OHLC frame for intraday-touch tests."""
    d0 = date(2025, 1, 1)
    dates = [pd.Timestamp(d0 + timedelta(days=i)) for i in range(n)]
    return pd.DataFrame({"close": closes, "high": highs, "low": lows}, index=dates)


def test_intraday_touch_exits_via_high_when_close_does_not_hit_target():
    """Flat SPY at 500 except day 30 has a 12% intraday spike up that fades
    back to close=500. A bull debit at entry 500 hits its profit target at
    day 30's high but not at any daily close — touch mode should exit with
    target_intraday on day 30; daily-close mode should ride to time_stop."""
    n = 60
    closes = [500.0] * n
    highs  = [500.0] * n
    lows   = [500.0] * n
    highs[30] = 560.0   # 12% intraday spike, faded back
    df = _intraday_df(n, closes, highs, lows)
    dates = list(df.index)

    held  = simulate_trade(df, dates, 0, "bull_debit", {})
    touch = simulate_trade(df, dates, 0, "bull_debit", {}, intraday_touch=True)

    assert held is not None and touch is not None
    assert held["exit_reason"]  in ("time_stop", "expiry")
    assert touch["exit_reason"] == "target_intraday"
    assert touch["days_held"] < held["days_held"]


def test_intraday_touch_default_off_byte_identical_to_old_behavior():
    """With intraday_touch=False (default), the function must produce the same
    output as before the parameter existed. Uses the existing _ramp_df helper
    (close-only frame) which is what every current caller passes."""
    df = _ramp_df(n=120, start=500.0, step=2.0)
    dates = list(df.index)
    a = simulate_trade(df, dates, 0, "bull_debit", {})                       # default
    b = simulate_trade(df, dates, 0, "bull_debit", {}, intraday_touch=False) # explicit
    assert a == b


def test_intraday_touch_pathological_no_range_matches_daily_close():
    """If high == low == close on every bar, intraday-touch mode has nothing
    new to discover and must produce identical output to daily-close mode."""
    n = 60
    closes = [500.0 + 2.0 * i for i in range(n)]
    df = _intraday_df(n, closes, list(closes), list(closes))
    dates = list(df.index)
    a = simulate_trade(df, dates, 0, "bull_debit", {})                          # touch off
    b = simulate_trade(df, dates, 0, "bull_debit", {}, intraday_touch=True)     # touch on, no range
    assert a == b
```

- [ ] **Step 2: Run tests, verify they FAIL**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_realistic_pricing.py -v -k "intraday_touch"`
Expected: 3 FAILS — `TypeError: simulate_trade() got an unexpected keyword argument 'intraday_touch'`

- [ ] **Step 3: Modify the `simulate_trade` signature**

In `backtests/realistic_pricing.py`, find the signature (currently ends `stop_loss_frac: float | None = None) -> dict | None:`). Add the new parameter as the LAST keyword argument:

```python
def simulate_trade(spy_df: pd.DataFrame, dates: list, entry_idx: int,
                   play: str, vix_at: dict,
                   entry_dte: int = ENTRY_DTE,
                   profit_target_pct: float = PROFIT_TARGET_PCT,
                   dte_close_threshold: int = DTE_CLOSE_THRESHOLD,
                   stop_loss_frac: float | None = None,
                   intraday_touch: bool = False) -> dict | None:
```

Then in the docstring (just after the `stop_loss_frac:` paragraph and before the `vix_at:` paragraph), add:

```python
    intraday_touch: if True, re-mark the spread at the day's HIGH and LOW in
    addition to the CLOSE on each iteration. If the best intraday mark hits
    profit_target_pct on a day where the daily-close mark did not, exit at
    that mark with exit_reason='target_intraday'. The stop check (if enabled)
    stays on the daily close — there is no broker-side hard stop in the live
    system, so live-realism is intraday-touch on the profit side only.
    Default False = byte-identical to the original daily-close behavior.
```

- [ ] **Step 4: Replace the walk-forward loop body**

Locate the loop starting with `# Walk forward until an exit rule fires or we run out of data.` (around line 162) and ending at the close of the `return {...}` block (around line 194). Replace the entire loop body (everything from `for j in range(entry_idx + 1, len(dates)):` through and including its `return {...}` block) with this:

```python
    # Walk forward until an exit rule fires or we run out of data.
    for j in range(entry_idx + 1, len(dates)):
        d   = dates[j]
        dte = (expiry - d).days
        vix = vix_at.get(d, vix0)

        # Mark at the close (always). Also mark at high/low when intraday_touch.
        def _pnl_at(spot: float) -> float:
            long_v, short_v = _net_value(legs, spot, vix, max(dte, 0))
            if credit:
                cost = max(0.0, short_v - long_v) + EXIT_SLIPPAGE     # pay to close (slipped worse)
                return (entry_px - cost) * 100
            proceeds = max(0.0, long_v - short_v) - EXIT_SLIPPAGE      # receive to close (slipped worse)
            return (proceeds - entry_px) * 100

        pnl_close = _pnl_at(float(spy_df.loc[d, "close"]))
        pnl_best  = pnl_close
        if intraday_touch:
            pnl_best = max(pnl_best,
                           _pnl_at(float(spy_df.loc[d, "high"])),
                           _pnl_at(float(spy_df.loc[d, "low"])))

        hit_target_close = max_profit > 0 and pnl_close / max_profit >= profit_target_pct
        hit_target_intra = (intraday_touch and not hit_target_close
                            and max_profit > 0
                            and pnl_best / max_profit >= profit_target_pct)
        hit_stop = (stop_loss_frac is not None and max_loss > 0
                    and pnl_close <= -stop_loss_frac * max_loss)

        if hit_target_close or hit_target_intra or hit_stop or dte <= dte_close_threshold or dte <= 0:
            pnl_exit = pnl_best if hit_target_intra else pnl_close
            net = pnl_exit - commission
            exit_reason = ("target_intraday" if hit_target_intra else
                           "target" if hit_target_close else
                           "stop"   if hit_stop else
                           "expiry" if dte <= 0 else "time_stop")
            return {
                "play":        play,
                "pnl_dollars": round(net, 2),
                "outcome":     "win" if net > 0 else "loss" if net < 0 else "breakeven",
                "exit_reason": exit_reason,
                "days_held":   (d - entry_date).days,
                "entry_px":    round(entry_px, 2),
            }
    return None  # ran off the end of the data
```

Two safety properties to preserve and verify after the edit:
1. With `intraday_touch=False`, only `pnl_close` is computed (the `if intraday_touch:` block is skipped), and `pnl_best == pnl_close`, so `hit_target_intra` is always False and the exit decision + price match today exactly.
2. The new code never reads `"high"` or `"low"` columns unless `intraday_touch=True`, so existing callers passing close-only frames (like `_ramp_df`) keep working.

- [ ] **Step 5: Run intraday-touch tests, verify all 3 PASS**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_realistic_pricing.py -v -k "intraday_touch"`
Expected: 3 passed.

- [ ] **Step 6: Run the FULL realistic-pricing test file to confirm no parity regressions**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_realistic_pricing.py -v`
Expected: 11 passed (8 pre-existing + 3 new).

- [ ] **Step 7: Commit**

```bash
git add backtests/realistic_pricing.py tests/test_realistic_pricing.py
git commit -m "feat: intraday-touch exit option in realistic pricer (default off)"
```

---

## Task 3: Walk-forward harness — population pricing + Δ metrics

**Files:**
- Create: `backtests/intraday_touch_wf.py`
- Test: `tests/test_intraday_touch_wf.py`

Standalone script that prices every tradeable backtest day twice (touch off vs on), aggregates per-trade Δ$/trade, attribution %, IS/OOS split, and per-regime breakdown. Verdict-matrix logic is added in Task 4. The unit tests in this task use small synthetic inputs and do NOT run the heavy backtest.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_intraday_touch_wf.py
import os, sys
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backtests.intraday_touch_wf import compare_runs, split_oos


def _trades_off():
    return pd.DataFrame([
        {"date": pd.Timestamp("2022-03-01"), "regime": "trending_up_calm",  "pnl_dollars": +100.0, "exit_reason": "target"},
        {"date": pd.Timestamp("2022-06-01"), "regime": "choppy_low_vol",    "pnl_dollars": -50.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2024-01-15"), "regime": "trending_up_calm",  "pnl_dollars": +80.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2025-02-01"), "regime": "choppy_low_vol",    "pnl_dollars": +30.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2025-09-01"), "regime": "trending_up_calm",  "pnl_dollars": -120.0, "exit_reason": "expiry"},
    ])


def _trades_on():
    # Same entry dates and regimes, but intraday-touch caught two peaks earlier.
    return pd.DataFrame([
        {"date": pd.Timestamp("2022-03-01"), "regime": "trending_up_calm",  "pnl_dollars": +100.0, "exit_reason": "target"},
        {"date": pd.Timestamp("2022-06-01"), "regime": "choppy_low_vol",    "pnl_dollars": -50.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2024-01-15"), "regime": "trending_up_calm",  "pnl_dollars": +160.0, "exit_reason": "target_intraday"},
        {"date": pd.Timestamp("2025-02-01"), "regime": "choppy_low_vol",    "pnl_dollars": +30.0,  "exit_reason": "time_stop"},
        {"date": pd.Timestamp("2025-09-01"), "regime": "trending_up_calm",  "pnl_dollars": +50.0,  "exit_reason": "target_intraday"},
    ])


def test_split_oos_by_date_fraction():
    """First 60% of entries by date count = in-sample, rest = OOS."""
    df = _trades_off()
    ins, oos = split_oos(df, fraction=0.6)
    assert len(ins) == 3 and len(oos) == 2
    assert ins["date"].max() < oos["date"].min()


def test_compare_runs_computes_deltas_attribution_and_per_regime():
    off, on = _trades_off(), _trades_on()
    result = compare_runs(off, on, oos_fraction=0.6)
    # Delta is per-trade mean of (on - off) on identical entry dates.
    # IS rows (first 3 entries): deltas = [0, 0, +80]; mean = 80/3 ≈ 26.67
    assert abs(result["is_delta_per_trade"] - (80.0 / 3)) < 0.01
    # OOS rows (last 2): deltas = [0, +170]; mean = 85.0
    assert abs(result["oos_delta_per_trade"] - 85.0) < 0.01
    # OOS baseline: (-120 + 30) / 2 = -45.0
    assert abs(result["oos_baseline_per_trade"] - (-45.0)) < 0.01
    # Attribution: 1 of 2 OOS exits in `on` is target_intraday => 0.5
    assert abs(result["oos_attribution"] - 0.5) < 0.01
    # Per-regime: trending_up_calm has 1 IS + 1 OOS in this slice
    rmap = result["per_regime"]
    assert "trending_up_calm" in rmap
    assert rmap["trending_up_calm"]["n"] == 3   # all three trending_up_calm trades
```

- [ ] **Step 2: Run tests, verify they FAIL**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_touch_wf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtests.intraday_touch_wf'`

- [ ] **Step 3: Create `backtests/intraday_touch_wf.py` with the metrics layer**

```python
"""
backtests/intraday_touch_wf.py -- Walk-forward comparison: intraday-touch exit
vs daily-close exit on the realistic-priced SPY backtest population.

Runs simulate_trade twice on identical entry days (touch off, touch on),
joins per-trade outcomes on date, splits 60/40 by entry date (early =
in-sample, late = out-of-sample), and aggregates Δ$/trade, attribution %, and
per-regime breakdown. Task 4 adds the six-preset verdict matrix on top.

Run:  python -m backtests.intraday_touch_wf
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger


def split_oos(trades: pd.DataFrame, fraction: float = 0.6) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split trades chronologically: first `fraction` = in-sample, rest = OOS."""
    df = trades.sort_values("date").reset_index(drop=True)
    cut = int(len(df) * fraction)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def compare_runs(trades_off: pd.DataFrame, trades_on: pd.DataFrame,
                 oos_fraction: float = 0.6) -> dict:
    """Compute Δ$/trade, attribution %, IS/OOS, per-regime breakdown.

    trades_off / trades_on each have columns: date, regime, pnl_dollars,
    exit_reason. They share entry dates 1:1; we inner-join on date.
    """
    off = trades_off.set_index("date")[["pnl_dollars", "regime", "exit_reason"]].rename(
        columns={"pnl_dollars": "pnl_off", "exit_reason": "reason_off"})
    on = trades_on.set_index("date")[["pnl_dollars", "exit_reason"]].rename(
        columns={"pnl_dollars": "pnl_on", "exit_reason": "reason_on"})
    j = off.join(on, how="inner").reset_index()
    j["delta"] = j["pnl_on"] - j["pnl_off"]

    ins, oos = split_oos(j, fraction=oos_fraction)
    attribution = float((oos["reason_on"] == "target_intraday").mean()) if len(oos) else 0.0

    per_regime = {}
    for r in sorted(j["regime"].unique()):
        sub = j[j["regime"] == r]
        per_regime[r] = {"n": int(len(sub)),
                         "delta_per_trade": float(sub["delta"].mean()) if len(sub) else 0.0}

    return {
        "n_total":                int(len(j)),
        "n_is":                   int(len(ins)),
        "n_oos":                  int(len(oos)),
        "is_delta_per_trade":     float(ins["delta"].mean()) if len(ins) else 0.0,
        "oos_delta_per_trade":    float(oos["delta"].mean()) if len(oos) else 0.0,
        "oos_baseline_per_trade": float(oos["pnl_off"].mean()) if len(oos) else 0.0,
        "oos_attribution":        attribution,
        "per_regime":             per_regime,
    }


def _price_population(spy_df, vix_df, regime_df, intraday_touch: bool) -> pd.DataFrame:
    """Price every tradeable regime day with the given mode; return per-trade rows."""
    from backtests.realistic_pricing import simulate_trade, _vix_lookup

    spy = spy_df.copy(); spy.index = pd.to_datetime(spy.index)
    dates = sorted(pd.to_datetime(spy.index))
    didx  = {d: i for i, d in enumerate(dates)}
    va    = _vix_lookup(dates, vix_df)

    rows = []
    for _, r in regime_df[regime_df["tradeable"] == True].iterrows():
        d = pd.to_datetime(r["date"])
        if d not in didx:
            continue
        play = r["play"]
        if play == "skip":
            continue
        out = simulate_trade(spy, dates, didx[d], play, va, intraday_touch=intraday_touch)
        if out is None:
            continue
        out["date"]   = d
        out["regime"] = r["regime"]
        rows.append(out)
    return pd.DataFrame(rows)


def run() -> dict:
    """Load 5yr data, price both modes, return the comparison dict. (No printing.)"""
    from backtests.spy_daily_backtest import BacktestDataLoader, SPYBacktest
    from data.event_calendar import EventCalendar

    spy_df, vix_df = BacktestDataLoader().load(years=5, source="local")
    regime_df = SPYBacktest(spy_df, vix_df, EventCalendar(), years=5).run()

    logger.info("Pricing daily-close mode (touch off)...")
    off = _price_population(spy_df, vix_df, regime_df, intraday_touch=False)
    logger.info(f"  {len(off)} trades")
    logger.info("Pricing intraday-touch mode (touch on)...")
    on  = _price_population(spy_df, vix_df, regime_df, intraday_touch=True)
    logger.info(f"  {len(on)} trades")

    return compare_runs(off, on)


def main():
    result = run()
    # Verdict matrix printing is added in Task 4. For now, print the raw metrics.
    print(result)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, verify they PASS**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_touch_wf.py -v`
Expected: 2 passed.

- [ ] **Step 5: Confirm the module imports cleanly**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -c "import backtests.intraday_touch_wf; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add backtests/intraday_touch_wf.py tests/test_intraday_touch_wf.py
git commit -m "feat: walk-forward harness for intraday-touch comparison (metrics layer)"
```

---

## Task 4: Verdict matrix — 6-preset evaluation + printed report

**Files:**
- Modify: `backtests/intraday_touch_wf.py` (add preset table + verdict logic + printed report)
- Modify: `tests/test_intraday_touch_wf.py` (add verdict-logic tests)

Adds the six named presets and the per-preset evaluation. The `default-2σ` preset reads its three floors from `config.py` so they're tunable in one place; the other five presets are hard-coded as learning-context constants in the harness.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_intraday_touch_wf.py`:

```python
from backtests.intraday_touch_wf import PRESETS, evaluate_preset


def _measured(dollar, baseline_dollar, attrib, is_delta):
    """Build a `measured` dict shaped like compare_runs() output."""
    return {
        "oos_delta_per_trade":    dollar,
        "oos_baseline_per_trade": baseline_dollar,
        "oos_attribution":        attrib,
        "is_delta_per_trade":     is_delta,
        "n_oos": 230,
    }


def test_evaluate_preset_strict_passes_only_on_large_signal():
    strict = next(p for p in PRESETS if p["name"] == "strict-3σ")
    weak   = _measured(dollar=30.0, baseline_dollar=100.0, attrib=0.30, is_delta=20.0)
    strong = _measured(dollar=50.0, baseline_dollar=100.0, attrib=0.40, is_delta=30.0)
    assert evaluate_preset(weak,   strict)["ship"] is False  # below $40 stat floor
    assert evaluate_preset(strong, strict)["ship"] is True


def test_evaluate_preset_default_uses_config_constants():
    """default-2σ floors come from config.INTRADAY_TOUCH_SHIP_MIN_* so they're tunable."""
    import config
    default = next(p for p in PRESETS if p["name"] == "default-2σ")
    assert default["stat_floor"]   == config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR
    assert default["scale_floor"]  == config.INTRADAY_TOUCH_SHIP_MIN_FRAC
    assert default["attrib_floor"] == config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB


def test_evaluate_preset_blocks_when_is_sanity_fails():
    """If is_sanity is on and IS delta is non-positive, the preset must NOT ship
    even if all dollar/scale/attribution floors pass."""
    default = next(p for p in PRESETS if p["name"] == "default-2σ")
    passes_others = _measured(dollar=50.0, baseline_dollar=100.0, attrib=0.40, is_delta=-5.0)
    out = evaluate_preset(passes_others, default)
    assert out["ship"] is False
    assert out["floors_met"]["is_sanity"] is False


def test_evaluate_preset_oos_only_ignores_is_direction():
    """oos-only preset has is_sanity=off; a negative IS delta must not block ship."""
    oos_only = next(p for p in PRESETS if p["name"] == "oos-only")
    m = _measured(dollar=50.0, baseline_dollar=100.0, attrib=0.40, is_delta=-5.0)
    assert evaluate_preset(m, oos_only)["ship"] is True


def test_evaluate_preset_attribution_strict_requires_30pct_attrib():
    attrib_strict = next(p for p in PRESETS if p["name"] == "attribution-strict")
    low_attrib  = _measured(dollar=30.0, baseline_dollar=100.0, attrib=0.20, is_delta=10.0)
    high_attrib = _measured(dollar=30.0, baseline_dollar=100.0, attrib=0.35, is_delta=10.0)
    assert evaluate_preset(low_attrib,  attrib_strict)["ship"] is False
    assert evaluate_preset(high_attrib, attrib_strict)["ship"] is True


def test_presets_table_has_exactly_six_entries():
    names = {p["name"] for p in PRESETS}
    assert names == {"strict-3σ", "default-2σ", "lenient-1.5σ",
                     "research-1σ", "attribution-strict", "oos-only"}
```

- [ ] **Step 2: Run tests, verify they FAIL**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_touch_wf.py -v -k "preset"`
Expected: 6 FAILS — `ImportError: cannot import name 'PRESETS' from 'backtests.intraday_touch_wf'`

- [ ] **Step 3: Add the preset table + evaluator + printed report to `backtests/intraday_touch_wf.py`**

Add the following code to `backtests/intraday_touch_wf.py`. Place the `PRESETS` table and `evaluate_preset` immediately AFTER the `compare_runs` function and BEFORE `_price_population`. Then replace the existing `def main():` with the printing version below.

```python
import config


PRESETS = [
    {"name": "strict-3σ",          "stat_floor": 40.0, "scale_floor": 0.15, "attrib_floor": 0.25, "is_sanity": True},
    {"name": "default-2σ",         "stat_floor": config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR,
                                   "scale_floor": config.INTRADAY_TOUCH_SHIP_MIN_FRAC,
                                   "attrib_floor": config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB,
                                   "is_sanity": True},
    {"name": "lenient-1.5σ",       "stat_floor": 15.0, "scale_floor": 0.05, "attrib_floor": 0.10, "is_sanity": True},
    {"name": "research-1σ",        "stat_floor": 10.0, "scale_floor": 0.05, "attrib_floor": 0.05, "is_sanity": False},
    {"name": "attribution-strict", "stat_floor": 20.0, "scale_floor": 0.10, "attrib_floor": 0.30, "is_sanity": True},
    {"name": "oos-only",           "stat_floor": config.INTRADAY_TOUCH_SHIP_MIN_DOLLAR,
                                   "scale_floor": config.INTRADAY_TOUCH_SHIP_MIN_FRAC,
                                   "attrib_floor": config.INTRADAY_TOUCH_SHIP_MIN_ATTRIB,
                                   "is_sanity": False},
]


def evaluate_preset(measured: dict, preset: dict) -> dict:
    """Check a measured-result dict against one preset's four floors.

    Returns {"ship": bool, "floors_met": {dollar, scale, attrib, is_sanity: bool}}.
    The scale floor is computed as a fraction of |baseline| so a near-zero
    baseline doesn't make the floor vacuous; if baseline is zero we require
    only the dollar floor for the scale gate (treated as met).
    """
    delta    = measured["oos_delta_per_trade"]
    baseline = measured["oos_baseline_per_trade"]
    attrib   = measured["oos_attribution"]
    is_delta = measured["is_delta_per_trade"]

    dollar_ok = delta >= preset["stat_floor"]
    if abs(baseline) > 1e-9:
        scale_ok = (delta / abs(baseline)) >= preset["scale_floor"]
    else:
        scale_ok = True   # baseline ~ 0; dollar floor is the only meaningful gate
    attrib_ok = attrib >= preset["attrib_floor"]
    is_ok     = (is_delta > 0) if preset["is_sanity"] else True

    return {
        "ship": dollar_ok and scale_ok and attrib_ok and is_ok,
        "floors_met": {"dollar": dollar_ok, "scale": scale_ok,
                       "attrib": attrib_ok, "is_sanity": is_ok},
    }


def print_verdict_matrix(measured: dict) -> dict:
    """Print the measured result + verdict for each of the 6 presets.
    Returns a dict {preset_name: ship_bool} for callers that want the verdicts."""
    print("\n" + "=" * 78)
    print("  INTRADAY-TOUCH EXIT — WALK-FORWARD VERDICT")
    print("=" * 78)
    print(f"  n_OOS = {measured['n_oos']}   n_IS = {measured['n_is']}")
    print(f"  measured:  IS Δ=${measured['is_delta_per_trade']:+.1f}/trade   "
          f"OOS Δ=${measured['oos_delta_per_trade']:+.1f}/trade  "
          f"({measured['oos_delta_per_trade']/abs(measured['oos_baseline_per_trade'])*100 if abs(measured['oos_baseline_per_trade'])>1e-9 else 0:+.1f}% of baseline)")
    print(f"             attribution = {measured['oos_attribution']*100:.1f}% of OOS exits via target_intraday")
    print(f"\n  {'preset':<20} {'dollar':>7} {'scale':>6} {'attrib':>7} {'IS':>4}   verdict")
    print("-" * 78)
    verdicts = {}
    for p in PRESETS:
        out = evaluate_preset(measured, p)
        f   = out["floors_met"]
        mark = lambda b: "✓" if b else "✗"
        binding = "  <- BINDING" if p["name"] == "default-2σ" else ""
        print(f"  {p['name']:<20} {mark(f['dollar']):>7} {mark(f['scale']):>6} "
              f"{mark(f['attrib']):>7} {mark(f['is_sanity']):>4}   "
              f"{'SHIP' if out['ship'] else 'no'}{binding}")
        verdicts[p["name"]] = out["ship"]

    print("\n  Per-regime Δ$/trade:")
    for r, info in measured["per_regime"].items():
        print(f"    {r:<22} n={info['n']:>4}   Δ=${info['delta_per_trade']:+.1f}/trade")
    print("=" * 78 + "\n")
    return verdicts


def main():
    result = run()
    print_verdict_matrix(result)
```

- [ ] **Step 4: Run tests, verify they PASS**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m pytest tests/test_intraday_touch_wf.py -v`
Expected: 8 passed (2 from Task 3 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_touch_wf.py tests/test_intraday_touch_wf.py
git commit -m "feat: 6-preset ship-bar verdict matrix for intraday-touch backtest"
```

---

## Task 5: Run on real data + BUILD_LOG verdict

**Files:**
- Run: `backtests/intraday_touch_wf.py` (no code change)
- Modify: `BUILD_LOG.md` (append a new entry under today's date)

- [ ] **Step 1: Run the harness on real data**

Run: `/home/nexus/Projects/stock-market-trading-assistant/.venv/bin/python -m backtests.intraday_touch_wf 2>&1 | tee /tmp/intraday_touch_verdict.txt`

Expected: prints the verdict matrix to stdout (and captures a copy at `/tmp/intraday_touch_verdict.txt` for the BUILD_LOG entry). Expect ~30-60s for the two backtest passes.

- [ ] **Step 2: Read the captured verdict**

Run: `cat /tmp/intraday_touch_verdict.txt | tail -40`

Inspect the table. Note specifically:
- OOS Δ$/trade and its % of baseline
- Attribution (% of OOS exits that fired as `target_intraday`)
- IS Δ$/trade
- Which presets show SHIP, which show no
- Per-regime breakdown (which regimes drive any gain)

- [ ] **Step 3: Append a BUILD_LOG entry**

Open `BUILD_LOG.md`. Find the line beginning with `---` immediately above the most recent dated entry (currently `## 2026-05-22 | Meta-labeling layer — built, validated, SHELVED`). Insert a new entry immediately after that `---` and before the meta-labeling heading, like so:

```markdown
---

## 2026-05-22 | Intraday-touch exit backtest — <SHIPS / SHELVED / BORDERLINE>

The user asked: are we leaving intraday peaks on the table by only exiting
at the daily close? Built and ran the walk-forward to find out.

**What was built:**
- `intraday_touch: bool = False` parameter on `realistic_pricing.simulate_trade`
  (default off, byte-identical to old behavior verified by test). When on,
  re-marks the spread at the day's HIGH and LOW in addition to the CLOSE; if
  the best intraday mark hits the profit target on a day the close mark did
  not, exits with `exit_reason="target_intraday"` at the better mark.
- `backtests/intraday_touch_wf.py` — walk-forward harness that prices the
  full 5yr SPY tradeable population twice (off / on), splits 60/40 by entry
  date, and evaluates the result against six named presets.

**The verdict (paste actual measured values from /tmp/intraday_touch_verdict.txt):**
  - OOS Δ$/trade: <fill in>     (<fill in>% of baseline)
  - Attribution:  <fill in>%
  - IS Δ$/trade:  <fill in>
  - Presets shipping: <list which of the 6 ship>
  - Binding (default-2σ): <SHIP / no>

**What the pattern says:**
  - <Interpret per the spec's "Patterns We're Looking For" table. e.g. "Strict
    fails, default ships, lenient ships → real but moderate edge. Per-regime
    breakdown shows the gain concentrated in <regime>." Or "Research fails →
    definitively no edge at any threshold." Or "OOS-only ships but default
    fails → lucky-OOS-window; the IS check correctly blocked us.">

**Honesty caveats baked in:**
  - Daily HIGH/LOW under-counts iron-condor touches (their best intraday mark
    is at minimum-deviation, not the extremes). If condors drive a fail/
    borderline result, option B (true intraday 5-min bars) is the upgrade.
  - Same-day exit is correctly NOT checked (entry is at the entry day's close;
    that day's high/low pre-date the trade).

**Next step:**
  - <If default-2σ ships:> next project: port intraday-touch into
    `learning/exit_manager._evaluate` so the live paper trader harvests
    intraday peaks. Small task.
  - <If default-2σ fails but lenient/research ship:> eyes-open call needed
    before any production change; capture the shape for future revisit.
  - <If research fails too:> shelve; document closed.
```

Fill in the bracketed `<...>` placeholders with the actual measured numbers from `/tmp/intraday_touch_verdict.txt` and pick the matching interpretation paragraph from the patterns table. **Whatever the verdict — even if every preset fails — write the entry honestly. A "no edge" outcome is the system working, not a failure.**

- [ ] **Step 4: Commit**

```bash
git add BUILD_LOG.md
git commit -m "docs: BUILD_LOG — intraday-touch exit walk-forward verdict"
```

> The `/tmp/intraday_touch_verdict.txt` capture is **not** committed (it's outside the repo and ephemeral). The BUILD_LOG entry is the durable record.

---

## Self-Review Notes

- **Spec coverage:**
  - `intraday_touch` param in `simulate_trade` → Task 2 ✓
  - Daily HIGH/LOW/CLOSE re-mark + best-mark logic → Task 2 ✓
  - Stop check stays on daily close → Task 2 (preserved in the refactor) ✓
  - Loop starts at `entry_idx + 1`, no same-day check → Task 2 (unchanged) ✓
  - New `target_intraday` exit reason → Task 2 ✓
  - Walk-forward script (`intraday_touch_wf.py`) → Tasks 3 + 4 ✓
  - 6 named presets → Task 4 ✓
  - `default-2σ` binding from config constants → Tasks 1 + 4 ✓
  - Per-regime breakdown → Task 3 (in `compare_runs`) + Task 4 (printed) ✓
  - Verdict matrix print → Task 4 ✓
  - Conservative-condor caveat documented → BUILD_LOG entry template in Task 5 ✓
  - Live `ExitManager` NOT changed → all tasks leave it untouched ✓
  - Tests: opt-in correctness, parity, pathological no-range, verdict logic → Tasks 2 + 3 + 4 ✓
- **Placeholders:** none. The BUILD_LOG entry template in Task 5 has `<...>` slots — those are *deliberate* placeholders for the implementer to fill in from the actual measured values, not work to defer.
- **Type consistency:** `compare_runs` returns dict keys `oos_delta_per_trade`, `oos_baseline_per_trade`, `oos_attribution`, `is_delta_per_trade`, `n_oos`, `n_is`, `n_total`, `per_regime`. `evaluate_preset` consumes those exact keys. `PRESETS` entries have `name / stat_floor / scale_floor / attrib_floor / is_sanity`. `evaluate_preset` returns `{ship, floors_met: {dollar, scale, attrib, is_sanity}}`. All consistent across tasks 3-4.
- **Dependency order:** Task 1 (config) → Task 2 (engine) → Task 3 (harness metrics) → Task 4 (verdict matrix) → Task 5 (run + record). Each task is independently testable.
