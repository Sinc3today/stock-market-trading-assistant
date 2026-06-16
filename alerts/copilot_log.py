"""alerts/copilot_log.py -- manual 'log a trade I built myself' helpers.

Bot plays log via the 'I placed it' button. This is the other path: a trade the
user constructed on Robinhood themselves (RH has no API, so they tell the bot).
The form posts strikes by slot — buy-call / sell-call / buy-put / sell-put — and
these helpers infer strategy + direction so the smart-stop watchdog can track it.

A screenshot (alerts.play_vision) PRE-FILLS this same form; prefill_from_extracted
maps a vision-extracted play onto the form slots.
"""
from __future__ import annotations


def _f(form: dict, key: str):
    v = (form.get(key) or "").strip() if isinstance(form.get(key), str) else form.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _leg(action: str, otype: str, strike: float, expiry):
    return {"action": action, "option_type": otype, "strike": float(strike),
            "expiry": expiry or None}


def build_live_trade_kwargs(form: dict) -> dict:
    """Turn the manual copilot log form into TradeRecorder.log_entry kwargs.

    Form keys: ticker, expiry, entry_price, max_profit, max_loss, and strike
    slots bc/sc/bp/sp (buy-call/sell-call/buy-put/sell-put; blank = absent).
    Raises ValueError if no legs were provided.
    """
    ticker = ((form.get("ticker") or "SPY").strip() or "SPY").upper()
    expiry = (form.get("expiry") or "").strip() or None
    bc, sc, bp, sp = _f(form, "bc"), _f(form, "sc"), _f(form, "bp"), _f(form, "sp")

    legs = []
    if bc is not None: legs.append(_leg("BUY", "CALL", bc, expiry))
    if sc is not None: legs.append(_leg("SELL", "CALL", sc, expiry))
    if bp is not None: legs.append(_leg("BUY", "PUT", bp, expiry))
    if sp is not None: legs.append(_leg("SELL", "PUT", sp, expiry))
    if not legs:
        raise ValueError("no legs provided — fill at least one strike")

    n_call = sum(x is not None for x in (bc, sc))
    n_put = sum(x is not None for x in (bp, sp))

    if n_call == 2 and n_put == 2:
        strategy, direction = "iron_condor", "neutral"
    elif n_call == 2 and n_put == 0:
        # call vertical: long lower strike = debit/bullish, else credit/bearish
        strategy, direction = ("debit_spread", "bullish") if bc < sc else ("credit_spread", "bearish")
    elif n_put == 2 and n_call == 0:
        # put vertical: long higher strike = debit/bearish, else credit/bullish
        strategy, direction = ("debit_spread", "bearish") if bp > sp else ("credit_spread", "bullish")
    elif len(legs) == 1:
        leg = legs[0]
        long_call_or_short_put = (leg["option_type"] == "CALL") == (leg["action"] == "BUY")
        strategy, direction = "single_leg", ("bullish" if long_call_or_short_put else "bearish")
    else:
        strategy, direction = "custom", "neutral"

    return {
        "ticker": ticker,
        "entry_price": _f(form, "entry_price") or 0.0,
        "size": 1,
        "trade_type": strategy,
        "strategy": strategy,
        "direction": direction,
        "mode": "swing",
        "legs": legs,
        "max_profit": _f(form, "max_profit"),
        "max_loss": _f(form, "max_loss"),
        "book": "live",
        "source": "user-manual",
        "notes": "[LIVE] manually logged via copilot",
    }


def _g(strike) -> str:
    """Strike -> compact string for a form field ('781', not '781.0')."""
    if strike is None:
        return ""
    return f"{float(strike):g}"


def prefill_from_extracted(extracted: dict) -> dict:
    """Map a vision-extracted play (alerts.play_vision.parse_reply output) onto
    the manual-form slots so the user just confirms what Claude read."""
    slots = {"bc": "", "sc": "", "bp": "", "sp": ""}
    for leg in (extracted.get("legs") or []):
        otype = (leg.get("option_type") or "").upper()
        action = (leg.get("action") or "").upper()
        is_buy = action.startswith("B")
        if otype.startswith("C"):
            slots["bc" if is_buy else "sc"] = _g(leg.get("strike"))
        elif otype.startswith("P"):
            slots["bp" if is_buy else "sp"] = _g(leg.get("strike"))
    ep = extracted.get("entry_price")
    mp = extracted.get("max_profit")
    ml = extracted.get("max_loss")
    return {
        "ticker": (extracted.get("ticker") or "SPY"),
        "expiry": (extracted.get("expiry") or ""),
        "entry_price": ("" if ep is None else f"{ep:g}"),
        "max_profit": ("" if mp is None else f"{mp:g}"),
        "max_loss": ("" if ml is None else f"{ml:g}"),
        **slots,
    }
