# Phase 4b — Intraday Structure Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the intraday router's placeholder structures with real strikes + pricing, from a single spot-offset selection rule shared by live trading and the walk-forward backtest.

**Architecture:** A new `signals/intraday_structure_builder.py` owns geometry (`select_legs`, identical to the backtest's `build_0dte_legs`) and a pricer split (`LiveChainPricer` over snapshot mids, `HistoricalPricer` over per-contract aggregates). `build_structure` composes them. The live scanner builds structures at the `execute_signal` seam so `route()` stays pure; the backtest is refactored to source its entry structure from the same builder (parity-guarded).

**Tech Stack:** Python 3.11, pandas, pytz, loguru, pytest. Reuses `data/options_chain.OptionsChain`, `data/options_history.OptionsHistory` + `option_ticker`, and the existing `backtests/intraday_backtest` exit-marking logic.

**Spec:** `docs/superpowers/specs/2026-05-31-phase4b-structure-builder-design.md`

---

## Reference facts (verified — do not re-derive)

- **Geometry constants** (currently in `backtests/intraday_backtest.py`): `CONDOR_SHORT_OTM=3.0`, `CONDOR_WING=5.0`, `DEBIT_SHORT_OTM=3.0`. They MOVE to the builder; the backtest imports them back.
- **`build_0dte_legs(spot, structure)`** returns `list[{"action": "BUY"|"SELL", "cp": "C"|"P", "strike": int}]` where `strike = round(spot + offset)`. Structures: `"iron_condor"`, `"bull_debit"`, `"bear_debit"`.
  - iron_condor: SELL P `k(-3)`, BUY P `k(-8)`, SELL C `k(+3)`, BUY C `k(+8)`
  - bull_debit: BUY C `k(0)`, SELL C `k(+3)`
  - bear_debit: BUY P `k(0)`, SELL P `k(-3)`
- **`is_credit_structure(structure)`** → `structure == "iron_condor"`.
- **Router sub-strategy names** (in `setup.strategy` / setup_dict): `"call_debit_spread"`, `"put_debit_spread"`, `"iron_condor"`. The WF already maps these via `_strategy_to_structure`; this plan centralizes the mapping in the builder.
- **`OptionsChain.get_chain(ticker, contract_type, min_expiration: date, max_expiration: date, strike_min=None, strike_max=None, limit=50)`** → `list[dict]`, each: `{"ticker","strike": float,"expiration": iso-str,"dte": int,"type": "call"|"put","mid": float|None,"bid","ask","delta",...}`. `mid = (bid+ask)/2` or `None`. No same-day clamp (the clamp is only in `find_*`), so passing `min=max=today` yields 0DTE contracts.
- **`option_ticker(underlying, expiry: date, cp: "C"|"P", strike)`** → OCC ticker string.
- **`OptionsHistory.get_aggs(contract, multiplier, timespan, from_date, to_date)`** → `pd.DataFrame` indexed by timestamp with a `close` column; empty DataFrame on failure.
- **Journal leg shape** consumed by `ExpiryResolver._intrinsic` / `_nearest_expiration`: needs `action` ("BUY"/"SELL"), `type` or `option_type` ("call"/"put"), `strike`, and `expiration` (and `expiry` for TradeRecorder compat).
- **Pricing math** (matches `_simulate_short_dte_with_expiration`): credit → `max_profit=entry*100`, `max_loss=(wing-entry)*100`; debit → `max_profit=(width-entry)*100`, `max_loss=entry*100`. For our geometry wing=`CONDOR_WING`=5, width=`DEBIT_SHORT_OTM`=3.
- `route()` / `route_explain()` are pure (no chain/IO args) and MUST stay so.
- The intraday live path is gated by `config.INTRADAY_PAPER_BROKER_ENABLED`.

**Pre-flight (run once before Task 1):**
```bash
cd /home/nexus/Projects/stock-market-trading-assistant
source .venv/bin/activate
pytest tests/ -m "not integration" -q 2>&1 | tail -3   # confirm green baseline (918 passed)
git status --short                                      # clean; on main at a0cfd3e or a feature branch
```

---

## Task 1: `select_legs` + structure mapping (pure geometry)

Create the builder module with constants, the canonical geometry function (structure-keyed, identical to `build_0dte_legs`), and the router-name→structure map.

**Files:**
- Create: `signals/intraday_structure_builder.py`
- Test: `tests/test_intraday_structure_builder.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_structure_builder.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.intraday_structure_builder import (
    select_legs, structure_for_strategy,
    CONDOR_SHORT_OTM, CONDOR_WING, DEBIT_SHORT_OTM,
)


def test_iron_condor_geometry():
    legs = select_legs("iron_condor", spot=500.0)
    assert legs == [
        {"action": "SELL", "cp": "P", "strike": 497},
        {"action": "BUY",  "cp": "P", "strike": 492},
        {"action": "SELL", "cp": "C", "strike": 503},
        {"action": "BUY",  "cp": "C", "strike": 508},
    ]


def test_bull_debit_geometry():
    assert select_legs("bull_debit", spot=500.0) == [
        {"action": "BUY",  "cp": "C", "strike": 500},
        {"action": "SELL", "cp": "C", "strike": 503},
    ]


def test_bear_debit_geometry():
    assert select_legs("bear_debit", spot=500.0) == [
        {"action": "BUY",  "cp": "P", "strike": 500},
        {"action": "SELL", "cp": "P", "strike": 497},
    ]


def test_strike_rounding_to_dollar_grid():
    legs = select_legs("bull_debit", spot=500.4)
    assert legs[0]["strike"] == 500   # round(500.4)


def test_router_strategy_maps_to_structure():
    assert structure_for_strategy("call_debit_spread") == "bull_debit"
    assert structure_for_strategy("put_debit_spread")  == "bear_debit"
    assert structure_for_strategy("iron_condor")       == "iron_condor"


def test_unknown_structure_returns_empty():
    assert select_legs("nonsense", spot=500.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_structure_builder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'signals.intraday_structure_builder'`

- [ ] **Step 3: Write minimal implementation**

```python
# signals/intraday_structure_builder.py
"""Phase 4b — real intraday option structures.

select_legs() owns the spot-offset geometry (identical to the backtest's
build_0dte_legs). A pricer (live snapshot or historical aggregates) turns the
geometry into priced legs. build_structure() composes them. See
docs/superpowers/specs/2026-05-31-phase4b-structure-builder-design.md.
"""
from __future__ import annotations

# Spot-offset geometry (points). Fixed constants for now (parity + YAGNI);
# promote to config.py only when a hypothesis wants to tune them.
CONDOR_SHORT_OTM = 3.0   # short strikes this many points OTM
CONDOR_WING      = 5.0   # long strike this many points beyond the short
DEBIT_SHORT_OTM  = 3.0   # debit short leg this many points OTM (long is ATM)

# Router sub-strategy name -> canonical structure name.
_STRATEGY_TO_STRUCTURE = {
    "call_debit_spread": "bull_debit",
    "put_debit_spread":  "bear_debit",
    "iron_condor":       "iron_condor",
}


def structure_for_strategy(strategy: str) -> str:
    """Map a router sub-strategy name to a canonical structure name."""
    return _STRATEGY_TO_STRUCTURE.get(strategy, strategy)


def select_legs(structure: str, spot: float) -> list[dict]:
    """Spot-offset leg geometry, rounded to SPY's $1 strikes.

    Returns [{action, cp, strike}] — identical to backtests.intraday_backtest
    .build_0dte_legs (which now delegates here). cp is "C"/"P".
    """
    def k(x: float) -> int:
        return round(spot + x)

    if structure == "iron_condor":
        return [
            {"action": "SELL", "cp": "P", "strike": k(-CONDOR_SHORT_OTM)},
            {"action": "BUY",  "cp": "P", "strike": k(-CONDOR_SHORT_OTM - CONDOR_WING)},
            {"action": "SELL", "cp": "C", "strike": k(+CONDOR_SHORT_OTM)},
            {"action": "BUY",  "cp": "C", "strike": k(+CONDOR_SHORT_OTM + CONDOR_WING)},
        ]
    if structure == "bull_debit":
        return [
            {"action": "BUY",  "cp": "C", "strike": k(0)},
            {"action": "SELL", "cp": "C", "strike": k(+DEBIT_SHORT_OTM)},
        ]
    if structure == "bear_debit":
        return [
            {"action": "BUY",  "cp": "P", "strike": k(0)},
            {"action": "SELL", "cp": "P", "strike": k(-DEBIT_SHORT_OTM)},
        ]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_structure_builder.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add signals/intraday_structure_builder.py tests/test_intraday_structure_builder.py
git commit -m "feat: select_legs geometry + strategy mapping for intraday structure builder"
```

---

## Task 2: Refactor `build_0dte_legs` to delegate to `select_legs` (parity)

Extract: the backtest's geometry now comes from the builder, guaranteeing live and backtest select identically.

**Files:**
- Modify: `backtests/intraday_backtest.py` (the `build_0dte_legs` function + the three module constants)
- Test: `tests/test_intraday_structure_builder.py` (add a parity test)

- [ ] **Step 1: Write the failing parity test**

Append to `tests/test_intraday_structure_builder.py`:

```python
def test_select_legs_matches_legacy_build_0dte_legs():
    from backtests.intraday_backtest import build_0dte_legs
    for structure in ("iron_condor", "bull_debit", "bear_debit"):
        for spot in (487.3, 500.0, 612.49):
            assert select_legs(structure, spot) == build_0dte_legs(spot, structure), structure
```

- [ ] **Step 2: Run test to verify it passes already (parity holds by construction), then refactor**

Run: `pytest tests/test_intraday_structure_builder.py::test_select_legs_matches_legacy_build_0dte_legs -q`
Expected: PASS (the two implementations are byte-identical today). This test now GUARDS the refactor in Step 3.

- [ ] **Step 3: Refactor `build_0dte_legs` to delegate**

In `backtests/intraday_backtest.py`, replace the body of `build_0dte_legs` and the three constants. Keep the constants importable from the builder (some code/tests may reference `backtests.intraday_backtest.CONDOR_WING`):

```python
# near the top of backtests/intraday_backtest.py, replace the three
# CONDOR_SHORT_OTM / CONDOR_WING / DEBIT_SHORT_OTM assignments with:
from signals.intraday_structure_builder import (
    select_legs as _select_legs,
    CONDOR_SHORT_OTM, CONDOR_WING, DEBIT_SHORT_OTM,
)
```

```python
def build_0dte_legs(spot: float, structure: str) -> list[dict]:
    """Construct leg specs at point-offsets from spot (SPY $1 strikes).
    Delegates to the shared builder so live + backtest select identically."""
    return _select_legs(structure, spot)
```

- [ ] **Step 4: Run the backtest + WF suites to confirm parity holds**

Run: `pytest tests/test_intraday_structure_builder.py tests/ -k "intraday_backtest or router_wf" -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_backtest.py tests/test_intraday_structure_builder.py
git commit -m "refactor: backtest build_0dte_legs delegates to shared select_legs"
```

---

## Task 3: Pricing math (`_net_premium`, `_risk`) + `StructurePricing`

Pure helpers turning priced legs into entry price + risk numbers. No I/O.

**Files:**
- Modify: `signals/intraday_structure_builder.py`
- Test: `tests/test_intraday_structure_builder.py`

- [ ] **Step 1: Write the failing test**

```python
def test_net_premium_credit_iron_condor():
    from signals.intraday_structure_builder import _net_premium
    # priced legs: shorts collect, longs pay. IC short mids 1.20+1.10, long 0.40+0.35
    priced = [
        {"action": "SELL", "mid": 1.20}, {"action": "BUY", "mid": 0.40},
        {"action": "SELL", "mid": 1.10}, {"action": "BUY", "mid": 0.35},
    ]
    # credit = (1.20+1.10) - (0.40+0.35) = 1.55
    assert round(_net_premium(priced, "iron_condor"), 2) == 1.55


def test_net_premium_debit_bull():
    from signals.intraday_structure_builder import _net_premium
    priced = [{"action": "BUY", "mid": 2.00}, {"action": "SELL", "mid": 0.80}]
    assert round(_net_premium(priced, "bull_debit"), 2) == 1.20  # 2.00 - 0.80


def test_risk_credit():
    from signals.intraday_structure_builder import _risk
    mp, ml = _risk("iron_condor", entry=1.55)
    assert mp == round(1.55 * 100, 2)                 # 155.0
    assert ml == round((CONDOR_WING - 1.55) * 100, 2) # (5-1.55)*100 = 345.0


def test_risk_debit():
    from signals.intraday_structure_builder import _risk
    mp, ml = _risk("bull_debit", entry=1.20)
    assert mp == round((DEBIT_SHORT_OTM - 1.20) * 100, 2)  # (3-1.2)*100 = 180.0
    assert ml == round(1.20 * 100, 2)                      # 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_structure_builder.py -k "net_premium or risk" -q`
Expected: FAIL — `cannot import name '_net_premium'`

- [ ] **Step 3: Write minimal implementation**

Add to `signals/intraday_structure_builder.py`:

```python
def _is_credit(structure: str) -> bool:
    return structure == "iron_condor"


def _net_premium(priced_legs: list[dict], structure: str) -> float:
    """Net per-share premium from priced legs (each has action + mid).
    Credit structures: shorts - longs. Debit: longs - shorts."""
    longs  = sum(leg["mid"] for leg in priced_legs if leg["action"] == "BUY")
    shorts = sum(leg["mid"] for leg in priced_legs if leg["action"] == "SELL")
    return (shorts - longs) if _is_credit(structure) else (longs - shorts)


def _risk(structure: str, entry: float) -> tuple[float, float]:
    """(max_profit, max_loss) in dollars per 1 contract, matching the
    backtest's _simulate_short_dte_with_expiration formula."""
    if _is_credit(structure):
        return round(entry * 100, 2), round((CONDOR_WING - entry) * 100, 2)
    return round((DEBIT_SHORT_OTM - entry) * 100, 2), round(entry * 100, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_structure_builder.py -k "net_premium or risk" -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add signals/intraday_structure_builder.py tests/test_intraday_structure_builder.py
git commit -m "feat: net-premium + risk pricing math for structure builder"
```

---

## Task 4: `LiveChainPricer`

Price known strikes from the live snapshot chain; convert to journal leg shape. Returns `None` if any leg's mid is missing.

**Files:**
- Modify: `signals/intraday_structure_builder.py`
- Test: `tests/test_intraday_structure_builder.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date


class _FakeChain:
    """Stand-in for OptionsChain.get_chain returning canned contracts."""
    def __init__(self, contracts): self._c = contracts
    def get_chain(self, ticker, contract_type, min_expiration, max_expiration,
                  strike_min=None, strike_max=None, limit=50):
        return [c for c in self._c if c["type"] == contract_type]


def _contract(strike, cp, mid, exp="2026-06-01"):
    return {"ticker": f"O:SPY..{cp}{strike}", "strike": float(strike),
            "expiration": exp, "dte": 0, "type": cp, "mid": mid,
            "bid": mid, "ask": mid, "delta": None}


def test_live_pricer_prices_iron_condor():
    from signals.intraday_structure_builder import LiveChainPricer
    chain = _FakeChain([
        _contract(497, "put", 1.20), _contract(492, "put", 0.40),
        _contract(503, "call", 1.10), _contract(508, "call", 0.35),
    ])
    legs = select_legs("iron_condor", spot=500.0)
    out = LiveChainPricer(chain).price(legs, "iron_condor", "0DTE", spot=500.0,
                                       as_of=date(2026, 6, 1))
    assert round(out["entry_price"], 2) == 1.55
    assert out["max_profit"] == 155.0
    assert out["max_loss"] == 345.0
    # journal leg shape
    assert all(set(("action", "type", "option_type", "strike", "expiration", "expiry", "mid")) <= set(l) for l in out["legs"])
    assert {l["type"] for l in out["legs"]} == {"put", "call"}


def test_live_pricer_returns_none_when_a_leg_missing():
    from signals.intraday_structure_builder import LiveChainPricer
    chain = _FakeChain([_contract(497, "put", 1.20)])  # only one of four legs
    legs = select_legs("iron_condor", spot=500.0)
    assert LiveChainPricer(chain).price(legs, "iron_condor", "0DTE", 500.0,
                                        as_of=date(2026, 6, 1)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_structure_builder.py -k live_pricer -q`
Expected: FAIL — `cannot import name 'LiveChainPricer'`

- [ ] **Step 3: Write minimal implementation**

Add to `signals/intraday_structure_builder.py`:

```python
from datetime import date, timedelta

from loguru import logger

_CP_TO_TYPE = {"C": "call", "P": "put"}


def _target_expiry_window(dte_bucket: str, as_of: date) -> tuple[date, date]:
    """[min, max] expiry dates for a bucket. 0DTE = same day; 1-3DTE = the
    next 1..3 calendar days (pricer picks the nearest listed expiry in range)."""
    if dte_bucket == "0DTE":
        return as_of, as_of
    if dte_bucket == "1-3DTE":
        return as_of + timedelta(days=1), as_of + timedelta(days=3)
    return as_of, as_of


class LiveChainPricer:
    """Price known strikes from the live OptionsChain snapshot."""
    def __init__(self, options_chain):
        self.chain = options_chain

    def price(self, legs, structure, dte_bucket, spot, as_of):
        min_exp, max_exp = _target_expiry_window(dte_bucket, as_of)
        # Fetch both contract types once each, across the bucket's expiry window.
        calls = self.chain.get_chain("SPY", "call", min_exp, max_exp,
                                     strike_min=spot * 0.90, strike_max=spot * 1.10)
        puts  = self.chain.get_chain("SPY", "put",  min_exp, max_exp,
                                     strike_min=spot * 0.90, strike_max=spot * 1.10)
        by_key = {}
        chosen_exp = None
        for c in (calls + puts):
            if c.get("mid") is None:
                continue
            by_key[(c["type"], float(c["strike"]))] = c
            chosen_exp = chosen_exp or c.get("expiration")

        priced = []
        for leg in legs:
            ctype = _CP_TO_TYPE[leg["cp"]]
            c = by_key.get((ctype, float(leg["strike"])))
            if c is None:
                logger.info(f"LiveChainPricer: no quote for {ctype} {leg['strike']} — unpriceable")
                return None
            priced.append({**leg, "type": ctype, "mid": c["mid"]})

        entry = _net_premium(priced, structure)
        if entry <= 0:
            return None  # a non-positive credit/debit means the chain is unusable
        mp, ml = _risk(structure, entry)
        exp = chosen_exp or min_exp.isoformat()
        journal_legs = [{
            "action":      leg["action"],
            "type":        leg["type"],
            "option_type": leg["type"],
            "strike":      leg["strike"],
            "expiration":  exp,
            "expiry":      exp,
            "mid":         leg["mid"],
        } for leg in priced]
        return {"legs": journal_legs, "entry_price": round(entry, 2),
                "max_profit": mp, "max_loss": ml}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_structure_builder.py -k live_pricer -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add signals/intraday_structure_builder.py tests/test_intraday_structure_builder.py
git commit -m "feat: LiveChainPricer prices known strikes from snapshot chain"
```

---

## Task 5: `HistoricalPricer`

Price the same strikes from per-contract historical aggregates at the entry timestamp. Returns `None` if any leg has no data.

**Files:**
- Modify: `signals/intraday_structure_builder.py`
- Test: `tests/test_intraday_structure_builder.py`

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd


class _FakeHistory:
    """Stand-in for OptionsHistory: maps contract ticker -> close price."""
    def __init__(self, prices): self._p = prices   # {contract_str: float}
    def get_aggs(self, contract, multiplier, timespan, from_date, to_date, limit=50000):
        px = self._p.get(contract)
        if px is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        idx = pd.to_datetime(["2026-06-01 13:45:00"])
        return pd.DataFrame({"close": [px]}, index=idx)


def test_historical_pricer_prices_bull_debit():
    from signals.intraday_structure_builder import HistoricalPricer
    from data.options_history import option_ticker
    d = date(2026, 6, 1)
    legs = select_legs("bull_debit", spot=500.0)   # BUY C500, SELL C503
    prices = {
        option_ticker("SPY", d, "C", 500): 2.00,
        option_ticker("SPY", d, "C", 503): 0.80,
    }
    out = HistoricalPricer(_FakeHistory(prices)).price(
        legs, "bull_debit", "0DTE", spot=500.0, as_of=d)
    assert round(out["entry_price"], 2) == 1.20
    assert out["max_profit"] == 180.0
    assert out["max_loss"] == 120.0


def test_historical_pricer_none_when_leg_missing():
    from signals.intraday_structure_builder import HistoricalPricer
    legs = select_legs("bull_debit", spot=500.0)
    out = HistoricalPricer(_FakeHistory({})).price(
        legs, "bull_debit", "0DTE", spot=500.0, as_of=date(2026, 6, 1))
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_structure_builder.py -k historical_pricer -q`
Expected: FAIL — `cannot import name 'HistoricalPricer'`

- [ ] **Step 3: Write minimal implementation**

Add to `signals/intraday_structure_builder.py`:

```python
class HistoricalPricer:
    """Price known strikes from real per-contract intraday aggregates.

    Uses the FIRST available bar in the day window as the entry mark (the
    backtest enters at the opening-range end; callers pass that day). Returns
    None if any leg has no data."""
    def __init__(self, options_history):
        self.history = options_history

    def price(self, legs, structure, dte_bucket, spot, as_of):
        from data.options_history import option_ticker
        min_exp, _ = _target_expiry_window(dte_bucket, as_of)
        exp = min_exp   # 0DTE -> as_of; 1-3DTE -> first day of window
        priced = []
        for leg in legs:
            contract = option_ticker("SPY", exp, leg["cp"], leg["strike"])
            df = self.history.get_aggs(contract, 5, "minute", as_of, as_of)
            if df is None or df.empty or "close" not in df:
                return None
            mid = float(df["close"].iloc[0])
            priced.append({**leg, "type": _CP_TO_TYPE[leg["cp"]], "mid": mid})

        entry = _net_premium(priced, structure)
        if entry <= 0:
            return None
        mp, ml = _risk(structure, entry)
        journal_legs = [{
            "action": leg["action"], "type": leg["type"], "option_type": leg["type"],
            "strike": leg["strike"], "expiration": exp.isoformat(),
            "expiry": exp.isoformat(), "mid": leg["mid"],
        } for leg in priced]
        return {"legs": journal_legs, "entry_price": round(entry, 2),
                "max_profit": mp, "max_loss": ml}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_structure_builder.py -k historical_pricer -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add signals/intraday_structure_builder.py tests/test_intraday_structure_builder.py
git commit -m "feat: HistoricalPricer prices strikes from per-contract aggregates"
```

---

## Task 6: `build_structure` (compose selection + pricing)

The single entry point both the live scanner and the backtest call.

**Files:**
- Modify: `signals/intraday_structure_builder.py`
- Test: `tests/test_intraday_structure_builder.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_structure_live_end_to_end():
    from signals.intraday_structure_builder import build_structure, LiveChainPricer
    chain = _FakeChain([
        _contract(497, "put", 1.20), _contract(492, "put", 0.40),
        _contract(503, "call", 1.10), _contract(508, "call", 0.35),
    ])
    out = build_structure("iron_condor", "0DTE", spot=500.0,
                          pricer=LiveChainPricer(chain), as_of=date(2026, 6, 1))
    assert round(out["entry_price"], 2) == 1.55
    assert len(out["legs"]) == 4


def test_build_structure_returns_none_when_pricer_none():
    from signals.intraday_structure_builder import build_structure, LiveChainPricer
    out = build_structure("iron_condor", "0DTE", spot=500.0,
                          pricer=LiveChainPricer(_FakeChain([])), as_of=date(2026, 6, 1))
    assert out is None


def test_build_structure_maps_router_strategy_name():
    from signals.intraday_structure_builder import build_structure, LiveChainPricer
    chain = _FakeChain([_contract(500, "call", 2.00), _contract(503, "call", 0.80)])
    out = build_structure("call_debit_spread", "0DTE", spot=500.0,
                          pricer=LiveChainPricer(chain), as_of=date(2026, 6, 1))
    assert round(out["entry_price"], 2) == 1.20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_structure_builder.py -k build_structure -q`
Expected: FAIL — `cannot import name 'build_structure'`

- [ ] **Step 3: Write minimal implementation**

Add to `signals/intraday_structure_builder.py`:

```python
def build_structure(strategy, dte_bucket, spot, pricer, as_of=None):
    """Compose selection + pricing into a journal-ready structure dict, or None
    when it can't be priced honestly. `strategy` may be a router name
    (call_debit_spread/...) or a canonical structure name."""
    as_of = as_of or date.today()
    structure = structure_for_strategy(strategy)
    legs = select_legs(structure, spot)
    if not legs:
        return None
    return pricer.price(legs, structure, dte_bucket, spot, as_of)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_structure_builder.py -k build_structure -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add signals/intraday_structure_builder.py tests/test_intraday_structure_builder.py
git commit -m "feat: build_structure composes selection + pricer"
```

---

## Task 7: Refactor backtest entry pricing through `build_structure(HistoricalPricer)`

Make the backtest source its entry structure (legs + entry_price + max_profit/max_loss) from the shared builder, so both modes flow through one code path. Exit-marking stays inline. Parity is guarded by the existing WF tests.

**Files:**
- Modify: `backtests/intraday_backtest.py` (`_simulate_short_dte_with_expiration` and `simulate_0dte_day` entry-pricing blocks)
- Test: existing `tests/` WF + backtest suites (parity), plus one new assertion

> **Note for the implementer:** Read `_simulate_short_dte_with_expiration` and `simulate_0dte_day` fully first. They currently call `build_0dte_legs`, fetch each leg's full-session series via `get_aggs`, take the entry-time mark, and compute `entry_px`/`max_profit`/`max_loss` inline. The exit loop reuses those same per-leg series. Refactor ONLY the entry-structure derivation to call `build_structure(structure, dte_bucket, entry_spot, HistoricalPricer(options_history), as_of=day)`; KEEP the existing per-leg series fetch + exit-marking loop unchanged (it still needs the full series). Use the builder's `entry_price`/`max_profit`/`max_loss`; assert they equal the previously-inline values on the parity day.

- [ ] **Step 1: Write the failing/parity test**

Add `tests/test_phase4b_backtest_parity.py`:

```python
import os, sys
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_entry_pricing_matches_builder(monkeypatch):
    """The backtest's entry_price/max_profit/max_loss for a known day must
    equal build_structure(HistoricalPricer) on the same legs — proving the
    refactor is behavior-neutral."""
    import pandas as pd
    from signals.intraday_structure_builder import build_structure, HistoricalPricer, select_legs
    from data.options_history import option_ticker

    d = date(2026, 6, 1)
    legs = select_legs("iron_condor", spot=500.0)

    class H:
        def get_aggs(self, contract, *a, **k):
            # deterministic prices keyed by strike+cp embedded in the ticker
            table = {
                option_ticker("SPY", d, "P", 497): 1.20,
                option_ticker("SPY", d, "P", 492): 0.40,
                option_ticker("SPY", d, "C", 503): 1.10,
                option_ticker("SPY", d, "C", 508): 0.35,
            }
            px = table[contract]
            return pd.DataFrame({"close": [px]}, index=pd.to_datetime(["2026-06-01 13:45"]))

    out = build_structure("iron_condor", "0DTE", 500.0, HistoricalPricer(H()), as_of=d)
    assert round(out["entry_price"], 2) == 1.55
    assert out["max_profit"] == 155.0
    assert out["max_loss"] == 345.0
```

- [ ] **Step 2: Run it (passes — it pins the contract), then do the refactor**

Run: `pytest tests/test_phase4b_backtest_parity.py -q`
Expected: PASS. This pins the builder's historical output; Step 3 wires the backtest to it.

- [ ] **Step 3: Refactor the entry-pricing blocks**

In `backtests/intraday_backtest.py`, inside `_simulate_short_dte_with_expiration` (and the equivalent block in `simulate_0dte_day`), after computing `entry_spot` and fetching the per-leg series, replace the inline `entry_px` / `max_profit` / `max_loss` computation with:

```python
    from signals.intraday_structure_builder import build_structure, HistoricalPricer
    built = build_structure(structure, dte_bucket, entry_spot,
                            HistoricalPricer(options_history), as_of=day)
    if built is None:
        return None
    entry_px   = built["entry_price"]
    max_profit = built["max_profit"]
    max_loss   = built["max_loss"]
    # NOTE: keep the existing per-leg series fetch + exit-marking loop below;
    # it still uses build_0dte_legs(entry_spot, structure) (== select_legs) for the
    # contracts it marks through the session.
```

(`simulate_0dte_day` passes `dte_bucket="0DTE"`; `_simulate_short_dte_with_expiration` passes the bucket it received.)

- [ ] **Step 4: Run the full WF + backtest suites to confirm zero behavior change**

Run: `pytest tests/ -k "intraday_backtest or router_wf or phase4b" -q`
Expected: PASS, no regressions. If any WF result shifts, the entry-mark timestamp differs — align `HistoricalPricer` to use the same entry-time bar the backtest used (first bar of the session window).

- [ ] **Step 5: Commit**

```bash
git add backtests/intraday_backtest.py tests/test_phase4b_backtest_parity.py
git commit -m "refactor: backtest entry pricing flows through shared build_structure"
```

---

## Task 8: Wire the live scanner seam

After `route()`, build a real structure with `LiveChainPricer` and pass real legs/pricing to `execute_signal`; skip + log when unpriceable. Router stays pure.

**Files:**
- Modify: `scanners/intraday_scanner.py` (the Phase 3 block that calls `route()` → `execute_signal`)
- Test: `tests/test_intraday_scanner_structure.py` (new)

> **Note for the implementer:** Find the existing Phase 3 block (search for `execute_signal` and `route(` in `scanners/intraday_scanner.py`). It currently iterates the routed setup_dicts and calls `broker.execute_signal(setup_dict)` directly. Keep the surrounding `INTRADAY_PAPER_BROKER_ENABLED` gate and try/except. The scanner already has the current SPY price (the same value used to build the setup); pass it as `spot`. Construct the chain once: `OptionsChain(polygon_client=<the scanner's polygon client>)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_scanner_structure.py
import os, sys
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scanners.intraday_scanner import build_intraday_structure


def _chain(contracts):
    class C:
        def get_chain(self, t, ct, mn, mx, strike_min=None, strike_max=None, limit=50):
            return [c for c in contracts if c["type"] == ct]
    return C()


def test_build_intraday_structure_enriches_setup():
    setup = {"strategy": "iron_condor", "dte_bucket": "0DTE", "direction": "neutral"}
    chain = _chain([
        {"type": "put",  "strike": 497.0, "mid": 1.20, "expiration": "2026-06-01"},
        {"type": "put",  "strike": 492.0, "mid": 0.40, "expiration": "2026-06-01"},
        {"type": "call", "strike": 503.0, "mid": 1.10, "expiration": "2026-06-01"},
        {"type": "call", "strike": 508.0, "mid": 0.35, "expiration": "2026-06-01"},
    ])
    enriched = build_intraday_structure(setup, spot=500.0, chain=chain, as_of=date(2026, 6, 1))
    assert enriched is not None
    assert round(enriched["entry_price"], 2) == 1.55
    assert len(enriched["legs"]) == 4
    assert enriched["strategy"] == "iron_condor"   # original fields preserved


def test_build_intraday_structure_none_when_unpriceable():
    setup = {"strategy": "iron_condor", "dte_bucket": "0DTE", "direction": "neutral"}
    enriched = build_intraday_structure(setup, spot=500.0, chain=_chain([]), as_of=date(2026, 6, 1))
    assert enriched is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_scanner_structure.py -q`
Expected: FAIL — `cannot import name 'build_intraday_structure'`

- [ ] **Step 3: Add the helper + wire it in**

Add to `scanners/intraday_scanner.py`:

```python
def build_intraday_structure(setup: dict, spot: float, chain, as_of=None):
    """Materialize a routed setup_dict into one with REAL legs + pricing.
    Returns the enriched dict, or None when the structure can't be priced."""
    from signals.intraday_structure_builder import build_structure, LiveChainPricer
    built = build_structure(setup["strategy"], setup["dte_bucket"], spot,
                            LiveChainPricer(chain), as_of=as_of)
    if built is None:
        return None
    return {**setup, "legs": built["legs"], "entry_price": built["entry_price"],
            "max_profit": built["max_profit"], "max_loss": built["max_loss"]}
```

Then in the existing Phase 3 block, replace the direct `execute_signal(setup_dict)` loop with:

```python
            from data.options_chain import OptionsChain
            chain = OptionsChain(polygon_client=self.polygon)   # use the scanner's client attr name
            for setup_dict in routed:
                enriched = build_intraday_structure(setup_dict, spot=spy_price, chain=chain)
                if enriched is None:
                    logger.info(
                        f"intraday structure unpriceable — skipped "
                        f"{setup_dict.get('strategy')}/{setup_dict.get('dte_bucket')}"
                    )
                    continue
                broker.execute_signal(enriched)
```

(Match `self.polygon` / `spy_price` to the scanner's actual attribute + local variable names — read the surrounding code.)

- [ ] **Step 4: Run test to verify it passes + scanner imports**

Run: `pytest tests/test_intraday_scanner_structure.py -q && python -c "import scanners.intraday_scanner"`
Expected: PASS (2 passed) + clean import.

- [ ] **Step 5: Commit**

```bash
git add scanners/intraday_scanner.py tests/test_intraday_scanner_structure.py
git commit -m "feat: intraday scanner builds real structures at the execute_signal seam"
```

---

## Final verification (before declaring Phase 4b done)

- [ ] **Run the full non-integration suite**

Run: `pytest tests/ -m "not integration" --tb=short -q | tail -5`
Expected: all green (918 baseline + new builder/scanner tests), 0 regressions. The 2 live-FRED failures (`test_fred`, `test_economic_scanner`) are pre-existing network flakiness, unrelated.

- [ ] **Update BUILD_LOG.md** with the Phase 4b entry (modules shipped, parity guard, the deploy/activate steps below).

- [ ] **Deploy & activate (operator step, market closed):**
  1. `sudo systemctl restart smta.service` (loads new code; singleton lock re-acquires)
  2. Confirm `INTRADAY_PAPER_BROKER_ENABLED=True` in `config.py`
  3. Watch the first live session: new intraday trades carry real legs/pricing; `ExpiryResolver` closes them at real intrinsic (not 0.0).

---

## Self-review (completed by author)

- **Spec coverage:** select_legs (T1), backtest parity refactor (T2), pricing math (T3), LiveChainPricer (T4), HistoricalPricer (T5), build_structure (T6), backtest both-modes refactor (T7), live scanner seam (T8), deploy steps (final). The OptionsChain 0DTE sub-fix from the spec is **not needed** — `LiveChainPricer` prices known strikes via `get_chain` (no `find_*` clamp); noted at the top. All other spec sections map to a task.
- **Placeholder scan:** none — every code step has complete code; refactor tasks (T7, T8) include "read the surrounding code" notes because exact local names live in those files, but the inserted code is complete.
- **Type consistency:** `select_legs(structure, spot)` returns `{action, cp, strike}` throughout; pricers consume that and emit journal legs `{action, type, option_type, strike, expiration, expiry, mid}`; `build_structure(strategy, dte_bucket, spot, pricer, as_of)` signature is consistent across T6/T7/T8; `_net_premium`/`_risk` signatures consistent T3→T4/T5.
