"""learning/shadow_tester.py -- Extension-gate shadow-test (anti-bias).

On a day the regime's extension gate forces SKIP, record + score the
counterfactual bull trade the gate refused. See
docs/superpowers/specs/2026-06-03-extension-gate-shadow-test-design.md.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

import config
from signals.regime_detector import Regime

SHADOW_BOOK = "shadow"


def _is_extension_skip(regime_result) -> bool:
    """True only for the extension-gate skip (TRENDING_UP_CALM, not tradeable,
    reason mentions over-extension).

    Checks both `recommendation` and `play` attributes so it works with
    both test SimpleNamespace objects (which use `recommendation`) and
    real RegimeResult dataclasses (which use `play`).
    """
    rec = (
        getattr(regime_result, "recommendation", None)
        or getattr(regime_result, "play", None)
        or ""
    ).lower()
    return (
        getattr(regime_result, "regime", None) == Regime.TRENDING_UP_CALM
        and not getattr(regime_result, "tradeable", True)
        and ("extended" in rec or "extension" in rec)
    )
