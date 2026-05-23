"""
learning/hypothesis_runner.py -- Auto-backtest a hypothesis spec.

Takes a hypothesis JSON produced by hypothesis_engine.py, monkey-patches
the target module's variable, runs the SPY daily backtest in-process,
compares to a baseline run, and writes results back to the spec.

Accept rule (conservative, can be tuned):
    sharpe_delta > +0.10 AND pnl_delta > 0   -> accepted
    sharpe_delta < -0.10 OR pnl_delta < -250 -> rejected
    otherwise                                -> inconclusive

The runner does NOT modify any source files. Acceptance just means
"the data backs this change" -- promoting it to live config is a
deliberate human step (or a future approval workflow in the web app).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import date
from typing import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.knowledge_base   import KnowledgeBase, KBEntry
from learning.hypothesis_engine import TUNABLE_PARAMS


SHARPE_ACCEPT_DELTA   = 0.10
SHARPE_REJECT_DELTA   = -0.10
PNL_REJECT_DELTA      = -250
MIN_OOS_TRADES        = 30    # below this floor in the OOS slice → auto-inconclusive

BACKTEST_YEARS = 5


class HypothesisRunner:
    """Runs one (or all pending) hypotheses through the backtest."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        backtest_fn:    Callable | None      = None,
        post_fn:        Callable[[str], None] | None = None,
    ):
        self.kb = knowledge_base or KnowledgeBase()
        # Injectable for tests; default is the real backtest closure
        self._backtest_fn = backtest_fn or self._default_backtest
        # Notifier — fired when a verdict is "accepted" so the user knows
        # there's something ready to promote. Skipped if None.
        self._post_fn = post_fn

    # ── PUBLIC API ────────────────────────────────────

    def run_pending(self) -> list[dict]:
        hdir = os.path.join(config.LOG_DIR, "learning", "hypotheses")
        if not os.path.isdir(hdir):
            return []
        ran: list[dict] = []
        for fn in sorted(os.listdir(hdir)):
            if not (fn.startswith("hyp_") and fn.endswith(".json")):
                continue
            spec_path = os.path.join(hdir, fn)
            with open(spec_path) as f:
                spec = json.load(f)
            if spec.get("status") != "proposed":
                continue
            try:
                self.run(spec, spec_path=spec_path)
                ran.append(spec)
            except Exception as e:
                logger.error(f"HypothesisRunner: {spec.get('id')} failed -- {e}")
                spec["status"]  = "error"
                spec["backtest"] = {"error": str(e)}
                with open(spec_path, "w") as f:
                    json.dump(spec, f, indent=2)
        return ran

    def run(self, spec: dict, spec_path: str | None = None) -> dict:
        if not self._is_safe(spec):
            raise ValueError(f"Hypothesis {spec.get('id')} targets non-whitelisted param")

        baseline = self._backtest_fn(override=None)
        modified = self._backtest_fn(override=(spec["module"], spec["var"], spec["proposed_value"]))

        deltas = self._deltas(baseline, modified)
        verdict = self._verdict(deltas, modified)

        spec["backtest"] = {
            "baseline":  baseline,
            "modified":  modified,
            "deltas":    deltas,
            "verdict":   verdict,
            "run_date":  date.today().isoformat(),
        }
        spec["status"] = verdict

        if spec_path:
            with open(spec_path, "w") as f:
                json.dump(spec, f, indent=2)

        self.kb.append(KBEntry(
            date       = date.today().isoformat(),
            category   = "backtest_result",
            claim      = f"{spec.get('title','?')} -> {verdict}",
            evidence   = (
                f"OOS Δsharpe {deltas['oos_sharpe_delta']:+.3f}, "
                f"OOS Δpnl {deltas['oos_pnl_delta']:+}, "
                f"OOS trades {modified['oos']['trades']}; "
                f"IS Δsharpe {deltas['is_sharpe_delta']:+.3f}, "
                f"IS Δpnl {deltas['is_pnl_delta']:+}"
            ),
            confidence = 0.8 if verdict != "inconclusive" else 0.4,
            source     = "hypothesis_runner",
            tags       = [verdict, spec["module"], spec["var"]],
        ))
        logger.info(
            f"HypothesisRunner: {spec.get('id')} -> {verdict} "
            f"(sharpe {deltas['sharpe_delta']:+.3f}, pnl {deltas['pnl_delta']:+})"
        )

        # If the verdict is "accepted", surface it to the user so they
        # know there's a promote command waiting. Without this ping the
        # whole self-learning loop has a silent last mile.
        if verdict == "accepted" and self._post_fn:
            try:
                self._post_fn(
                    f"**Hypothesis accepted: {spec.get('id')}**\n"
                    f"{spec.get('module')}.{spec.get('var')}: "
                    f"{spec.get('current_value')} → {spec.get('proposed_value')}\n"
                    f"OOS ΔSharpe {deltas['oos_sharpe_delta']:+.2f} · "
                    f"OOS ΔP&L {deltas['oos_pnl_delta']:+,} "
                    f"(n={modified['oos']['trades']} OOS trades)\n\n"
                    f"Apply with: python -m learning.promote {spec.get('id')}"
                )
            except Exception as e:
                logger.warning(f"HypothesisRunner: accept notify failed: {e}")

        return spec

    # ── VERDICT ───────────────────────────────────────

    @staticmethod
    def _deltas(baseline: dict, modified: dict) -> dict:
        """Compute OOS deltas (used by verdict), IS deltas (context for KB),
        and aggregate deltas (back-compat with existing KB-entry readers)."""
        return {
            # OOS deltas — what the verdict gates on.
            "oos_trades_delta":   modified["oos"]["trades"]   - baseline["oos"]["trades"],
            "oos_win_rate_delta": round(modified["oos"]["win_rate"] - baseline["oos"]["win_rate"], 2),
            "oos_pnl_delta":      modified["oos"]["pnl"]      - baseline["oos"]["pnl"],
            "oos_sharpe_delta":   round(modified["oos"]["sharpe"] - baseline["oos"]["sharpe"], 3),
            # IS deltas — context only.
            "is_pnl_delta":       modified["is"]["pnl"]      - baseline["is"]["pnl"],
            "is_sharpe_delta":    round(modified["is"]["sharpe"] - baseline["is"]["sharpe"], 3),
            # Aggregate deltas — back-compat with existing KB-entry consumers.
            "trades_delta":       modified["trades"]   - baseline["trades"],
            "win_rate_delta":     round(modified["win_rate"] - baseline["win_rate"], 2),
            "pnl_delta":          modified["pnl"]      - baseline["pnl"],
            "sharpe_delta":       round(modified["sharpe"] - baseline["sharpe"], 3),
        }

    @staticmethod
    def _verdict(deltas: dict, modified: dict) -> str:
        """OOS-based verdict with sample-size floor.

        Auto-inconclusive if the affected OOS slice has < MIN_OOS_TRADES (30)
        — small samples can't honestly support either acceptance or rejection.
        """
        if modified["oos"]["trades"] < MIN_OOS_TRADES:
            return "inconclusive"
        oos_sd = deltas["oos_sharpe_delta"]
        oos_pd = deltas["oos_pnl_delta"]
        if oos_sd >= SHARPE_ACCEPT_DELTA and oos_pd > 0:
            return "accepted"
        if oos_sd <= SHARPE_REJECT_DELTA or oos_pd <= PNL_REJECT_DELTA:
            return "rejected"
        return "inconclusive"

    @staticmethod
    def _is_safe(spec: dict) -> bool:
        return (spec.get("module"), spec.get("var")) in TUNABLE_PARAMS

    # ── BACKTEST CLOSURE ──────────────────────────────

    @staticmethod
    def _default_backtest(override: tuple | None) -> dict:
        """
        Run the SPY daily backtest, optionally with a module-var override.
        Returns full-history stats PLUS in-sample (first 60% of dates) and
        out-of-sample (last 40% of dates) blocks. The verdict reads OOS.

        Override is restored in a finally block so a failed run can't leak
        a patched value into other modules.
        """
        import numpy as np
        import pandas as pd
        from backtests.spy_daily_backtest import SPYBacktest, BacktestDataLoader
        from data.event_calendar import EventCalendar

        original = None
        target_module = None
        if override is not None:
            module_path, var_name, new_value = override
            target_module = importlib.import_module(module_path)
            original = getattr(target_module, var_name)
            setattr(target_module, var_name, new_value)

        try:
            loader = BacktestDataLoader()
            spy_df, vix_df = loader.load(years=BACKTEST_YEARS, source="local")
            cal = EventCalendar()
            df  = SPYBacktest(spy_df, vix_df, cal, years=BACKTEST_YEARS).run()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            # 60/40 chronological split — matches every other walk-forward we've used.
            cut       = int(len(df) * 0.60)
            is_slice  = df.iloc[:cut]
            oos_slice = df.iloc[cut:]

            full = HypothesisRunner._metrics_block(df)
            full["is"]  = HypothesisRunner._metrics_block(is_slice)
            full["oos"] = HypothesisRunner._metrics_block(oos_slice)
            return full
        finally:
            if override is not None and target_module is not None:
                setattr(target_module, override[1], original)

    @staticmethod
    def _metrics_block(df) -> dict:
        """Compute {trades, win_rate, pnl, sharpe} for a slice of backtest rows."""
        import numpy as np
        traded = df[df["tradeable"] == True]
        closed = traded[traded["outcome"].isin(["win", "loss", "breakeven"])]
        n      = len(closed)
        wins   = len(closed[closed["outcome"] == "win"])
        wr     = round(wins / n * 100, 1) if n else 0.0
        pnl    = int(traded["pnl"].sum()) if len(traded) else 0
        daily  = traded["pnl"].values
        sharpe = float((np.mean(daily) / (np.std(daily) + 1e-9)) * np.sqrt(252)) if len(daily) > 1 else 0.0
        return {
            "trades":   int(n),
            "win_rate": wr,
            "pnl":      pnl,
            "sharpe":   round(sharpe, 3),
        }
