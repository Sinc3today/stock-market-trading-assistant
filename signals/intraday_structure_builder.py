"""Phase 4b — real intraday option structures.

select_legs() owns the spot-offset geometry (identical to the backtest's
build_0dte_legs). A pricer (live snapshot or historical aggregates) turns the
geometry into priced legs. build_structure() composes them. See
docs/superpowers/specs/2026-05-31-phase4b-structure-builder-design.md.
"""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

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


# ---------------------------------------------------------------------------
# Task 4: LiveChainPricer
# ---------------------------------------------------------------------------

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

        # Group contracts by expiry: {expiry_iso -> {(type, strike): contract}}
        by_expiry: dict[str, dict] = {}
        for c in (calls + puts):
            if c.get("mid") is None:
                continue
            exp_iso = c.get("expiration")
            if exp_iso is None:
                continue
            by_expiry.setdefault(exp_iso, {})[(c["type"], float(c["strike"]))] = c

        # Pick the NEAREST (ascending) expiry at which every leg has a quote.
        required_keys = [(_CP_TO_TYPE[leg["cp"]], float(leg["strike"])) for leg in legs]
        chosen_exp = None
        for exp_iso in sorted(by_expiry):
            key_map = by_expiry[exp_iso]
            if all(k in key_map for k in required_keys):
                chosen_exp = exp_iso
                break

        if chosen_exp is None:
            logger.info(f"LiveChainPricer: no single expiry covers all legs for {structure} — unpriceable")
            return None

        # Resolve all legs from the single chosen expiry.
        key_map = by_expiry[chosen_exp]
        priced = []
        for leg in legs:
            ctype = _CP_TO_TYPE[leg["cp"]]
            c = key_map.get((ctype, float(leg["strike"])))
            if c is None:
                logger.info(f"LiveChainPricer: no quote for {ctype} {leg['strike']} — unpriceable")
                return None
            priced.append({**leg, "type": ctype, "mid": c["mid"]})

        entry = _net_premium(priced, structure)
        if entry <= 0:
            logger.warning(f"LiveChainPricer: non-positive entry {entry:.3f} for {structure} — skipping")
            return None
        mp, ml = _risk(structure, entry)
        journal_legs = [{
            "action":      leg["action"],
            "type":        leg["type"],
            "option_type": leg["type"],
            "strike":      leg["strike"],
            "expiration":  chosen_exp,
            "expiry":      chosen_exp,
            "mid":         leg["mid"],
        } for leg in priced]
        return {"legs": journal_legs, "entry_price": round(entry, 2),
                "max_profit": mp, "max_loss": ml}


# ---------------------------------------------------------------------------
# Task 5: HistoricalPricer
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task 6: build_structure
# ---------------------------------------------------------------------------

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
