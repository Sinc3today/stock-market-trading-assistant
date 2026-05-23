"""
signals/regime_lens.py -- Regime-per-timeframe lens abstraction.

Each strategy reads conditions through the lens appropriate to its horizon:
  - 45DTE swing strategies → DailyLens (wraps signals.regime_detector.RegimeDetector)
  - 0DTE / 1-3DTE intraday strategies → IntradayLens (wraps signals.spy_options_engine.SPYOptionsEngine)

LENS_FOR_STRATEGY is the (strategy, dte_bucket) -> lens-class mapping that
Phase 3+ consumers (intraday paper-broker, per-sub-strategy entry gates) read
to know which lens to instantiate for the trade they're considering.

This module is FORMALIZATION only — no functional change to existing behavior.
The bot's current 09:15 daily play path continues to call RegimeDetector
directly; the new lens classes are available for Phase 3+ to use.
"""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class RegimeLens(ABC):
    """Abstract base — both DailyLens and IntradayLens implement read(...)."""

    @abstractmethod
    def read(self, **kwargs) -> Any:
        """Read current conditions. Return type depends on the lens:
          - DailyLens.read() returns a RegimeResult (regime label + tradeable + metrics)
          - IntradayLens.read() returns a list[SPYSetup] (per-strategy candidates with scores)
        """
        ...


class DailyLens(RegimeLens):
    """Wraps signals.regime_detector.RegimeDetector — the existing daily regime classifier."""

    def read(self, *, spy_daily_df, vix_current: float, ivr_current: float,
             today=None) -> "RegimeResult":
        from signals.regime_detector import RegimeDetector
        from datetime import date
        return RegimeDetector().classify(
            spy_daily_df = spy_daily_df,
            vix_current  = vix_current,
            ivr_current  = ivr_current,
            today        = today or date.today(),
        )


class IntradayLens(RegimeLens):
    """Wraps signals.spy_options_engine.SPYOptionsEngine — the existing intraday signal engine."""

    def read(self, *, df_15m, df_5m) -> "list":
        from signals.spy_options_engine import SPYOptionsEngine
        return SPYOptionsEngine().analyze(df_15m, df_5m)


# ── Strategy → Lens registry ──────────────────────────────────────────────
# Each (strategy, dte_bucket) entry resolves to the lens class that strategy
# reads. Phase 3+ consumers look up the right lens via lens_for(...).

LENS_FOR_STRATEGY: dict[tuple[str, str], type[RegimeLens]] = {
    # 45DTE swing — daily lens
    ("call_debit_spread", "45DTE"): DailyLens,
    ("put_debit_spread",  "45DTE"): DailyLens,
    ("iron_condor",       "45DTE"): DailyLens,

    # 1-3DTE — intraday lens
    ("call_debit_spread", "1-3DTE"): IntradayLens,
    ("put_debit_spread",  "1-3DTE"): IntradayLens,
    ("iron_condor",       "1-3DTE"): IntradayLens,

    # 0DTE — intraday lens
    ("call_debit_spread", "0DTE"): IntradayLens,
    ("put_debit_spread",  "0DTE"): IntradayLens,
    ("iron_condor",       "0DTE"): IntradayLens,
}


def lens_for(strategy: str, dte_bucket: str) -> type[RegimeLens] | None:
    """Look up which lens class a given (strategy, dte_bucket) reads through.
    Returns None for unknown combinations — caller decides whether to default
    or error. Phase 2b ships this as discovery infrastructure only; no caller
    invokes lens_for() yet (Phase 3+ will)."""
    return LENS_FOR_STRATEGY.get((strategy, dte_bucket))
