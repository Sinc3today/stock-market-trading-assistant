"""data/market_quotes.py -- real NBBO option quotes + live mark-to-market.

The bot marks positions at Polygon day-close/vwap (no bid/ask spread, optimistic).
This pulls the real bid/ask off yfinance — the same NBBO Robinhood shows you,
broker-independent, free, ~15-min delayed (fine for slippage/MTM, not for live
order decisions). position_mtm() is pure math; fetch_leg_quotes() is the I/O.

Sign convention: a position's value to the holder = Σ(long mids) − Σ(short mids).
A credit structure opens at value −credit; a debit opens at +debit. MTM is the
change since open. spread_cost is what crossing the bid/ask to close would cost.
"""
from __future__ import annotations

from loguru import logger

OPTION_MULTIPLIER = 100


def _is_long(leg) -> bool:
    return (leg.get("action") or "").upper().startswith("B")


def position_mtm(legs_with_quotes: list[dict], entry_price: float, size: int,
                 action: str) -> dict | None:
    """Live MTM of an options position from per-leg bid/ask/mid quotes.

    action="credit" (condor/credit spread, opened for a credit) or "debit"
    (debit spread / long, opened for a debit). Returns None if any leg lacks a
    usable mid (can't mark the structure honestly with a hole in it).
    """
    legs = legs_with_quotes or []
    if not legs or any(l.get("mid") is None for l in legs):
        return None

    # value to the holder, per share per contract
    current_value_mid = sum((l["mid"] if _is_long(l) else -l["mid"]) for l in legs)
    open_value = entry_price if action.lower() == "debit" else -entry_price
    mtm_per_share = current_value_mid - open_value

    # value realized if you cross the spread to close NOW: sell longs at the bid,
    # buy back shorts at the ask
    have_ba = all(l.get("bid") is not None and l.get("ask") is not None for l in legs)
    spread_cost_per_share = 0.0
    if have_ba:
        value_worst = sum((l["bid"] if _is_long(l) else -l["ask"]) for l in legs)
        spread_cost_per_share = current_value_mid - value_worst

    mult = size * OPTION_MULTIPLIER
    return {
        "current_value_mid": current_value_mid,
        "mtm_per_share": mtm_per_share,
        "mtm_dollars": mtm_per_share * mult,
        "spread_cost_per_share": spread_cost_per_share,
        "spread_cost_dollars": spread_cost_per_share * mult,
    }


def fetch_leg_quotes(ticker: str, legs: list[dict]) -> list[dict]:
    """Annotate each leg with bid/ask/mid from yfinance. One option_chain call
    per (expiry, right). Returns the legs (copies) with quote fields; mid is None
    where a quote couldn't be found. Network/best-effort."""
    import yfinance as yf

    out = [dict(l) for l in legs]
    tk = yf.Ticker(ticker)
    # group leg indices by (expiry, call/put) to minimize chain fetches
    groups: dict[tuple, list[int]] = {}
    for i, l in enumerate(out):
        exp = str(l.get("expiration") or l.get("expiry") or "")[:10]
        cp = (l.get("option_type") or l.get("type") or "").upper()
        groups.setdefault((exp, cp), []).append(i)

    for (exp, cp), idxs in groups.items():
        if not exp:
            continue
        try:
            chain = tk.option_chain(exp)
            df = chain.calls if cp.startswith("C") else chain.puts
            by_strike = {round(float(r.strike), 2): r for r in df.itertuples()}
        except Exception as e:
            logger.warning(f"market_quotes: chain {ticker} {exp} {cp} failed: {e}")
            continue
        for i in idxs:
            row = by_strike.get(round(float(out[i].get("strike")), 2))
            if row is None:
                continue
            bid = float(getattr(row, "bid", 0) or 0) or None
            ask = float(getattr(row, "ask", 0) or 0) or None
            out[i]["bid"], out[i]["ask"] = bid, ask
            out[i]["mid"] = round((bid + ask) / 2, 4) if (bid and ask) else None
    return out
