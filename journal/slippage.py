"""journal/slippage.py -- fill-quality (slippage) math + observation store.

The bot's paper marks use day-close/vwap — optimistic, because they ignore the
bid/ask spread a real order has to cross. Before trusting realized P&L with real
money, the user wants to know the true gap: what a real RH fill cost vs the mark
the bot assumed. This is the part we can build and verify without RH access; the
live RH quote fetch (Playwright) lives behind the seam in data/rh_session.py.

Convention: slippage_dollars > 0 means the real fill was WORSE than the mark
(money lost to the spread); < 0 means you actually did better than the mark.
"""
from __future__ import annotations

import json
import os

from atomic_io import atomic_write_text

OPTION_MULTIPLIER = 100


def compute_slippage(mark_price: float, fill_price: float, *, action: str,
                     contracts: int = 1, multiplier: int = OPTION_MULTIPLIER) -> dict:
    """Slippage of a real fill vs the assumed mark.

    action="credit"  — you RECEIVE premium (condor, credit spread): you want a
                       high price, so a lower fill is a cost.
    action="debit"   — you PAY premium (debit spread, long single): you want a
                       low price, so a higher fill is a cost.

    Returns per-share, total-dollar, and percent slippage (positive = worse).
    """
    act = action.lower()
    if act == "credit":
        per_share = mark_price - fill_price
    elif act == "debit":
        per_share = fill_price - mark_price
    else:
        raise ValueError(f"action must be 'credit' or 'debit', got {action!r}")
    dollars = per_share * contracts * multiplier
    pct = (per_share / mark_price * 100.0) if mark_price else 0.0
    return {
        "action": act,
        "mark_price": mark_price,
        "fill_price": fill_price,
        "contracts": contracts,
        "slippage_per_share": per_share,
        "slippage_dollars": dollars,
        "slippage_pct": pct,
    }


class SlippageStore:
    """Append-only JSONL of slippage observations. Rewrites atomically (crash/
    freeze-safe) — the set is small, so whole-file rewrite is fine and keeps the
    same durability guarantee as the rest of the journal."""

    def __init__(self, path: str):
        self.path = path

    def all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows

    def record(self, observation: dict) -> None:
        rows = self.all()
        rows.append(observation)
        text = "".join(json.dumps(r) + "\n" for r in rows)
        atomic_write_text(self.path, text)

    def summary(self) -> dict:
        rows = self.all()
        n = len(rows)
        if not n:
            return {"count": 0, "total_dollars": 0.0, "avg_dollars": 0.0, "avg_pct": 0.0}
        total = sum(r.get("slippage_dollars", 0.0) for r in rows)
        pct = sum(r.get("slippage_pct", 0.0) for r in rows)
        return {
            "count": n,
            "total_dollars": total,
            "avg_dollars": total / n,
            "avg_pct": pct / n,
        }
