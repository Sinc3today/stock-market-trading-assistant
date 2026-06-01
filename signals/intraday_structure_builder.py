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
    credit/debit formula in backtests/intraday_router_wf.py::_simulate_short_dte_with_expiration."""
    if _is_credit(structure):
        return round(entry * 100, 2), round((CONDOR_WING - entry) * 100, 2)
    return round((DEBIT_SHORT_OTM - entry) * 100, 2), round(entry * 100, 2)
