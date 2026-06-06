"""signals/exit_counterfactual.py -- did the exit help or hurt?

exit_quality = pnl_exit - pnl_hold.
  > 0  the exit SAVED money (we got out of a worse outcome) — good discipline.
  < 0  the exit COST money (we cut a trade that would have done better) — a bad
       hunch / premature exit.
Aggregated per (strategy, dte_bucket, exit_reason) it tells us whether a time-exit
rule is systematically saving losers or cutting winners. Feeds exit_timing KB.
"""
from __future__ import annotations


def exit_quality(pnl_exit: float, pnl_hold: float) -> float:
    """Signed dollars the exit decision was worth vs holding to EOD/expiry."""
    return round(pnl_exit - pnl_hold, 2)


def aggregate_exit_quality(rows: list[dict]) -> dict:
    """Group rows by 'strategy|dte_bucket|exit_reason'. Each row needs
    strategy, dte_bucket, exit_reason, pnl_exit, pnl_hold."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = f"{r['strategy']}|{r['dte_bucket']}|{r['exit_reason']}"
        groups.setdefault(key, []).append(r)

    out = {}
    for key, rs in groups.items():
        n = len(rs)
        eq = [exit_quality(r["pnl_exit"], r["pnl_hold"]) for r in rs]
        out[key] = {
            "n": n,
            "mean_exit_quality": round(sum(eq) / n, 2),
            "mean_pnl_exit": round(sum(r["pnl_exit"] for r in rs) / n, 2),
            "mean_pnl_hold": round(sum(r["pnl_hold"] for r in rs) / n, 2),
        }
    return out
