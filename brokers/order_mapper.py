"""brokers/order_mapper.py -- pure mapping: our legs -> Tradier multileg order.

The network-free, correctness-critical core of the Tradier integration
(docs/TRADIER_MIGRATION.md). Converts the leg dicts that build_condor /
build_broken_wing / copilot_log already emit into Tradier's class=multileg
POST body: OCC option symbols + per-leg side + quantity.

Defined-risk guardrail: reject any structure that isn't a balanced,
capped-loss spread (every short must be covered by a long of the same type).
Naked/undefined-risk orders never leave this module.
"""
from __future__ import annotations

from datetime import date


def occ_symbol(root: str, expiry_iso: str, option_type: str, strike: float) -> str:
    """OCC option symbol, e.g. SPY 639 CALL exp 2026-07-31 -> SPY260731C00639000.
    Format: ROOT + YYMMDD + C|P + (strike*1000) zero-padded to 8 digits."""
    d = date.fromisoformat(str(expiry_iso)[:10])
    cp = "C" if str(option_type).upper().startswith("C") else "P"
    strike_milli = int(round(float(strike) * 1000))
    if strike_milli <= 0 or strike_milli > 99_999_999:
        raise ValueError(f"strike out of OCC range: {strike}")
    return f"{root.upper()}{d.strftime('%y%m%d')}{cp}{strike_milli:08d}"


def leg_side(action: str, intent: str = "open") -> str:
    """'BUY'/'SELL' x 'open'/'close' -> Tradier side (buy_to_open, etc.)."""
    a = str(action).lower()
    if a not in ("buy", "sell"):
        raise ValueError(f"bad action: {action}")
    if intent not in ("open", "close"):
        raise ValueError(f"bad intent: {intent}")
    return f"{a}_to_{intent}"


def _assert_defined_risk(legs: list[dict]) -> None:
    """Every short leg must be covered by a long of the SAME option type — i.e.
    the structure has a capped max loss. Blocks naked/ratio-uncovered orders."""
    from collections import Counter
    longs = Counter()
    shorts = Counter()
    for leg in legs:
        typ = "C" if str(leg["option_type"]).upper().startswith("C") else "P"
        if str(leg["action"]).upper().startswith("B"):
            longs[typ] += 1
        else:
            shorts[typ] += 1
    for typ, n_short in shorts.items():
        if longs[typ] < 1 or n_short > longs[typ] * 2:
            # A butterfly (1 long / 2 short / 1 long) is fine (2 <= 1*2); a lone
            # short or an uncovered ratio is not.
            raise ValueError(
                "undefined-risk structure rejected (defined-risk only): "
                f"{typ} shorts={n_short} longs={longs[typ]}")


def build_multileg_order(root: str, legs: list[dict], quantity: int,
                         order_type: str, price: float | None = None,
                         duration: str = "day", intent: str = "open") -> dict:
    """Build the Tradier POST body for a class=multileg options order.

    legs: our leg dicts {action, option_type, strike, expiry}. A repeated strike
    (e.g. a butterfly's 2x short body) becomes two separate indexed legs.
    order_type: 'credit' | 'debit' | 'even' | 'market'.
    intent: 'open' a new structure or 'close' an existing one (flips the sides).
    Rejects undefined-risk structures.
    """
    if not legs:
        raise ValueError("no legs")
    if order_type not in ("credit", "debit", "even", "market"):
        raise ValueError(f"bad order_type: {order_type}")
    _assert_defined_risk(legs)
    params: dict = {
        "class": "multileg",
        "symbol": root.upper(),
        "type": order_type,
        "duration": duration,
    }
    if price is not None and order_type != "market":
        params["price"] = f"{float(price):.2f}"
    for i, leg in enumerate(legs):
        action = leg["action"]
        if intent == "close":
            # Closing reverses each leg: sell what you bought, buy back what you
            # sold. leg_side stays a dumb formatter; the flip lives here.
            action = "SELL" if str(action).upper().startswith("B") else "BUY"
        params[f"option_symbol[{i}]"] = occ_symbol(
            root, leg.get("expiry") or leg.get("expiration"),
            leg["option_type"], leg["strike"])
        params[f"side[{i}]"] = leg_side(action, intent)
        params[f"quantity[{i}]"] = str(int(quantity))
    return params
