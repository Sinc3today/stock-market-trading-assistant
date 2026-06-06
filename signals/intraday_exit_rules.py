"""signals/intraday_exit_rules.py -- the single, shared intraday exit decision.

Pure and stateless: both the backtest session loop and the live ExitManager call
evaluate_intraday_exit() so the rule the walk-forward validates is byte-identical
to the rule that runs live. Evaluation order:
    profit-target -> hard-stop -> scratch@T -> hard-close@T
The first rule that fires wins; None means "hold for now".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as _time


@dataclass(frozen=True)
class ExitDecision:
    """A fired exit. exit_price is the slippage-adjusted price to transact at."""
    exit_price: float
    reason:     str     # "target" | "stop" | "scratch" | "hard_close"
    fired_at:   str     # "HH:MM" ET when the rule fired (or "" for target/stop)


def _as_time(hhmm: str | None) -> _time | None:
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    return _time(int(h), int(m))


def evaluate_intraday_exit(position: dict, mark: dict, now_et: _time,
                           rule: dict, enable_time_exits: bool = True
                           ) -> ExitDecision | None:
    """Decide whether to close `position` now.

    position: {strategy, dte_bucket, max_profit, max_loss}
    mark:     {pnl: float (dollars), exit_price: float (per-share, slippage-adj)}
    now_et:   datetime.time of the current bar (ET)
    rule:     {profit_target_pct, stop_pct, scratch_time, scratch_theta,
               hard_close_time} — times are "HH:MM" strings or None.
    enable_time_exits: when False, the scratch/hard-close rules are skipped
               entirely (the live kill-switch path).
    """
    pnl        = mark.get("pnl")
    exit_price = mark.get("exit_price", 0.0)
    max_profit = position.get("max_profit")
    max_loss   = position.get("max_loss")

    # 1. Profit target — a working trade always takes its win first.
    if (rule.get("profit_target_pct") is not None and pnl is not None
            and max_profit and max_profit > 0
            and pnl / max_profit >= rule["profit_target_pct"]):
        return ExitDecision(exit_price, "target", "")

    # 2. Hard stop (where configured).
    if (rule.get("stop_pct") is not None and pnl is not None
            and max_loss and max_loss > 0
            and pnl <= -rule["stop_pct"] * max_loss):
        return ExitDecision(exit_price, "stop", "")

    if not enable_time_exits:
        return None

    # 3. Scratch — at/after scratch_time, bail only if it's not working.
    scratch_t = _as_time(rule.get("scratch_time"))
    if (scratch_t is not None and now_et >= scratch_t and pnl is not None
            and max_profit and max_profit > 0
            and pnl < rule.get("scratch_theta", 0.0) * max_profit):
        return ExitDecision(exit_price, "scratch", rule["scratch_time"])

    # 4. Hard close — at/after hard_close_time, close unconditionally.
    hard_t = _as_time(rule.get("hard_close_time"))
    if hard_t is not None and now_et >= hard_t:
        return ExitDecision(exit_price, "hard_close", rule["hard_close_time"])

    return None
