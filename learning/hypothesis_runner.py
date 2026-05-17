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

BACKTEST_YEARS = 5


class HypothesisRunner:
    """Runs one (or all pending) hypotheses through the backtest."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        backtest_fn:    Callable | None      = None,
    ):
        self.kb = knowledge_base or KnowledgeBase()
        # Injectable for tests; default is the real backtest closure
        self._backtest_fn = backtest_fn or self._default_backtest

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

        deltas = {
            "trades_delta":   modified["trades"]   - baseline["trades"],
            "win_rate_delta": round(modified["win_rate"] - baseline["win_rate"], 2),
            "pnl_delta":      modified["pnl"]      - baseline["pnl"],
            "sharpe_delta":   round(modified["sharpe"] - baseline["sharpe"], 3),
        }
        verdict = self._verdict(deltas)

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
                f"sharpe_delta {deltas['sharpe_delta']:+.3f}, "
                f"pnl_delta {deltas['pnl_delta']:+}, "
                f"win_rate_delta {deltas['win_rate_delta']:+.1f}%, "
                f"trades_delta {deltas['trades_delta']:+}"
            ),
            confidence = 0.8 if verdict != "inconclusive" else 0.4,
            source     = "hypothesis_runner",
            tags       = [verdict, spec["module"], spec["var"]],
        ))
        logger.info(
            f"HypothesisRunner: {spec.get('id')} -> {verdict} "
            f"(sharpe {deltas['sharpe_delta']:+.3f}, pnl {deltas['pnl_delta']:+})"
        )
        return spec

    # ── VERDICT ───────────────────────────────────────

    @staticmethod
    def _verdict(deltas: dict) -> str:
        sd = deltas["sharpe_delta"]
        pd = deltas["pnl_delta"]
        if sd >= SHARPE_ACCEPT_DELTA and pd > 0:
            return "accepted"
        if sd <= SHARPE_REJECT_DELTA or pd <= PNL_REJECT_DELTA:
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
        Returns {trades, win_rate, pnl, sharpe}.

        Override is restored in a finally block so a failed run can't leak
        a patched value into other modules.
        """
        import numpy as np
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

            traded = df[df["tradeable"] == True]
            wins   = len(traded[traded["outcome"] == "win"])
            closed = len(traded[traded["outcome"].isin(["win","loss","breakeven"])])
            wr     = round(wins / closed * 100, 1) if closed else 0.0
            pnl    = int(traded["pnl"].sum())
            daily  = traded["pnl"].values
            sharpe = float((np.mean(daily) / (np.std(daily) + 1e-9)) * np.sqrt(252)) if len(daily) else 0.0
            return {
                "trades":   int(closed),
                "win_rate": wr,
                "pnl":      pnl,
                "sharpe":   round(sharpe, 3),
            }
        finally:
            if override is not None and target_module is not None:
                setattr(target_module, override[1], original)
