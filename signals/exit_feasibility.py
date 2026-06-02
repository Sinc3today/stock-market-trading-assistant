"""signals/exit_feasibility.py -- dual-book routing predicate.

Routes a priced intraday entry to the disciplined book (the real-money proxy) or
the learning book (the falsification sandbox: the trades disciplined refuses,
taken in paper to gather disconfirming evidence). See
docs/superpowers/specs/2026-06-01-intraday-learning-isolation-design.md.
"""
from __future__ import annotations

import config

_PERMISSIVE = {"min_target_dollars": 0.0, "min_rr": 0.0}


def assign_book(strategy, dte_bucket, max_profit, max_loss, *, profit_target_pct) -> str:
    """Return "disciplined" or "learning" for a priced entry.

    Disciplined iff the sub-strategy's profit target is a meaningful dollar
    amount (profit_target_pct * max_profit >= min_target_dollars) AND the reward/
    risk (max_profit / max_loss) >= min_rr. Otherwise learning. Total function:
    an unconfigured combo uses a permissive default (disciplined); never raises.
    """
    th = config.INTRADAY_FEASIBILITY.get((strategy, dte_bucket), _PERMISSIVE)
    target = (profit_target_pct or 0.0) * (max_profit or 0.0)
    rr = (max_profit / max_loss) if max_loss and max_loss > 0 else 0.0
    if target >= th["min_target_dollars"] and rr >= th["min_rr"]:
        return "disciplined"
    return "learning"
