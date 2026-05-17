"""
signals/options_walls.py -- Support / resistance from heavy option strikes.

"Walls" are strikes with concentrated open interest. Dealers who sold
those options hedge dynamically, so price tends to:
  - stall / reverse at heavy CALL strikes (resistance ceiling)
  - bounce / find support at heavy PUT strikes (support floor)

Max pain is the strike where total option holder intrinsic value is
minimized — i.e. where option writers collectively pay out the least.
Price often gravitates toward max pain into expiry.

Public surface:

    compute_walls(chain_calls, chain_puts, spot, top_n=3)
        Returns {call_walls: [...], put_walls: [...], max_pain: float}.
        Each wall is {strike, open_interest, distance_pct}.

    load_walls(ticker, spot, dte_target=14, options_chain=None, ...)
        Convenience: fetches the chain via OptionsChain and computes walls.

Either function is safe to call when data is missing — empty / None
fields are returned rather than exceptions raised.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from loguru import logger


def compute_walls(
    chain_calls: list[dict],
    chain_puts:  list[dict],
    spot:        float | None,
    top_n:       int = 3,
) -> dict:
    """Pure aggregator. Caller supplies the normalized chains."""
    call_walls = _top_by_oi(chain_calls, top_n, spot, side="call")
    put_walls  = _top_by_oi(chain_puts,  top_n, spot, side="put")
    mp = max_pain(chain_calls, chain_puts)
    return {
        "call_walls": call_walls,
        "put_walls":  put_walls,
        "max_pain":   mp,
        "spot":       spot,
    }


def max_pain(chain_calls: list[dict], chain_puts: list[dict]) -> float | None:
    """
    Returns the strike (from the union of candidate strikes) where total
    option-holder intrinsic value is minimized. Returns None if data
    insufficient.
    """
    strikes = set()
    for c in chain_calls or []:
        s = c.get("strike")
        if s is not None: strikes.add(float(s))
    for p in chain_puts or []:
        s = p.get("strike")
        if s is not None: strikes.add(float(s))
    if not strikes:
        return None

    candidates = sorted(strikes)
    best_strike = None
    best_pain   = None
    for p in candidates:
        pain = _total_pain_at_price(p, chain_calls, chain_puts)
        if best_pain is None or pain < best_pain:
            best_pain, best_strike = pain, p
    return best_strike


def load_walls(
    ticker:         str,
    spot:           float,
    dte_target:     int = 14,
    dte_tolerance:  int = 7,
    top_n:          int = 3,
    options_chain        = None,
) -> dict:
    """
    Fetch the option chain for `ticker` around `dte_target` and compute
    walls. Returns the same shape as compute_walls() plus an `expiration`
    field naming the expiry window that was sampled, or an empty result
    if the chain is unavailable.
    """
    from data.options_chain import OptionsChain  # local import keeps cold-start cheap

    chain = options_chain or OptionsChain()
    min_exp = date.today() + timedelta(days=max(1, dte_target - dte_tolerance))
    max_exp = date.today() + timedelta(days=dte_target + dte_tolerance)

    try:
        calls = chain.get_chain(ticker, "call", min_exp, max_exp,
                                strike_min=spot * 0.85,
                                strike_max=spot * 1.15,
                                limit=200)
        puts  = chain.get_chain(ticker, "put",  min_exp, max_exp,
                                strike_min=spot * 0.85,
                                strike_max=spot * 1.15,
                                limit=200)
    except Exception as e:
        logger.warning(f"options_walls: chain fetch failed for {ticker}: {e}")
        return _empty(spot)

    if not calls and not puts:
        return _empty(spot)

    out = compute_walls(calls, puts, spot, top_n=top_n)
    # Use the most common expiration across the chain for the label
    exps = [c.get("expiration") for c in (calls + puts) if c.get("expiration")]
    out["expiration"] = max(set(exps), key=exps.count) if exps else None
    return out


# ── internals ──────────────────────────────────────

def _top_by_oi(contracts: list[dict], top_n: int, spot: float | None, side: str) -> list[dict]:
    if not contracts:
        return []
    ranked = sorted(
        [c for c in contracts if c.get("open_interest") is not None],
        key=lambda c: -int(c.get("open_interest") or 0),
    )[:top_n]
    out = []
    for c in ranked:
        strike = c.get("strike")
        if strike is None:
            continue
        out.append({
            "strike":        float(strike),
            "open_interest": int(c.get("open_interest") or 0),
            "distance_pct":  _dist_pct(spot, float(strike)),
            "side":          side,
        })
    return out


def _total_pain_at_price(p: float, calls: list[dict], puts: list[dict]) -> float:
    """Sum of intrinsic-value paid out to option holders if price = p at expiry."""
    pain = 0.0
    for c in calls or []:
        k  = c.get("strike");  oi = c.get("open_interest")
        if k is None or oi is None: continue
        pain += max(0.0, p - float(k)) * int(oi)
    for c in puts or []:
        k  = c.get("strike");  oi = c.get("open_interest")
        if k is None or oi is None: continue
        pain += max(0.0, float(k) - p) * int(oi)
    return pain


def _dist_pct(spot: float | None, level: float) -> float | None:
    if spot is None or spot == 0:
        return None
    return round((level - spot) / spot * 100, 2)


def _empty(spot: float | None) -> dict:
    return {
        "call_walls": [], "put_walls": [],
        "max_pain":   None,
        "spot":       spot,
        "expiration": None,
    }
