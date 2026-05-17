"""
learning/portfolio_greeks.py -- Aggregate Greeks across open positions.

The morning brief now picks real Polygon contracts with per-leg Greeks
(delta, gamma, theta, vega). This module rolls those up across every
open `[AUTO-PAPER]` (and real) trade in TradeRecorder so the dashboard
can show total exposure:

    Total delta:    +120     (long-biased)
    Total theta:    -$45/day (paying decay)
    Total vega:     -$180    (short volatility)

Used by:
    - /macro web page (fourth card)
    - macro_chat context bundle (lets Claude answer
      "what's my current delta exposure?")

Lookup rules:
    For each open trade, walk its legs. If a leg has `ticker` (Polygon
    contract ID), look it up live via OptionsChain. Otherwise the leg
    is "legacy theoretical" (predates the chain integration) and we
    skip it with a warning — total Greeks then reflects only positions
    we can actually price.

Sign convention:
    Buy leg  →   +Greeks  (long the leg)
    Sell leg →   -Greeks  (short the leg)

Multiply by trade size (contracts) before summing.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from typing import Iterable, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from journal.trade_recorder import TradeRecorder


# ── PER-CONTRACT MULTIPLIER ────────────────────────────
# Each option contract represents 100 shares; theta + vega flow is
# multiplied by 100 to express dollar impact, delta is multiplied
# to express share-equivalent exposure.
CONTRACT_MULTIPLIER = 100


class PortfolioGreeks:
    """Read-only aggregator across TradeRecorder open trades."""

    def __init__(
        self,
        trade_recorder = None,
        options_chain  = None,   # data.options_chain.OptionsChain (optional)
    ):
        self.trades = trade_recorder or TradeRecorder()
        self.chain  = options_chain

    # ── PUBLIC API ────────────────────────────────────

    def compute(self) -> dict:
        """
        Returns:
            {
              "as_of":         ISO timestamp,
              "total": {
                "delta":  float (share-equivalents),
                "gamma":  float,
                "theta":  float (dollars/day),
                "vega":   float (dollars per 1% IV move),
              },
              "positions": [
                {
                  "trade_id":   str,
                  "ticker":     str,
                  "strategy":   str,
                  "contracts":  int,
                  "legs":       [{action, strike, type, expiration,
                                  delta, theta, vega, ...}],
                  "delta":      float,
                  "theta":      float,
                  "vega":       float,
                  "gamma":      float,
                  "warning":    str | None,   # if any leg couldn't be priced
                },
                ...
              ],
              "skipped_legs":  int,   # count of legacy/un-priceable legs
              "open_trade_count": int,
            }
        """
        open_trades = self._safe(lambda: self.trades.get_open_trades(), default=[])
        positions:  list[dict] = []
        skipped:    int        = 0
        total = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

        for trade in open_trades:
            pos, leg_skips = self._aggregate_trade(trade)
            skipped += leg_skips
            positions.append(pos)
            for k in total:
                v = pos.get(k)
                if isinstance(v, (int, float)):
                    total[k] += v

        # Round for display
        total_rounded = {k: round(v, 3) for k, v in total.items()}

        return {
            "as_of":           datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total":           total_rounded,
            "positions":       positions,
            "skipped_legs":    skipped,
            "open_trade_count": len(open_trades),
        }

    # ── AGGREGATION ───────────────────────────────────

    def _aggregate_trade(self, trade: dict) -> tuple[dict, int]:
        legs       = trade.get("legs") or []
        contracts  = int(trade.get("size") or 1)
        skipped    = 0
        priced_legs: list[dict] = []
        running = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

        for leg in legs:
            priced = self._price_leg(leg)
            if not priced:
                skipped += 1
                continue
            sign = +1 if (leg.get("action") or "").lower() == "buy" else -1
            for k in running:
                v = priced.get(k)
                if isinstance(v, (int, float)):
                    running[k] += sign * v * contracts * CONTRACT_MULTIPLIER
            priced_legs.append({
                "action":     leg.get("action"),
                "type":       priced.get("type") or leg.get("type"),
                "strike":     priced.get("strike") or leg.get("strike"),
                "expiration": priced.get("expiration") or leg.get("expiration"),
                "ticker":     priced.get("ticker") or leg.get("ticker"),
                "delta":      priced.get("delta"),
                "theta":      priced.get("theta"),
                "vega":       priced.get("vega"),
                "gamma":      priced.get("gamma"),
            })

        warning = None
        if skipped and not priced_legs:
            warning = f"All {skipped} legs lack Polygon contract data (legacy trade)"
        elif skipped:
            warning = f"{skipped} of {len(legs)} legs un-priced — totals partial"

        return {
            "trade_id":   trade.get("trade_id"),
            "ticker":     trade.get("ticker"),
            "strategy":   trade.get("strategy") or trade.get("trade_type"),
            "contracts":  contracts,
            "legs":       priced_legs,
            "delta":      round(running["delta"], 3),
            "gamma":      round(running["gamma"], 3),
            "theta":      round(running["theta"], 3),
            "vega":       round(running["vega"], 3),
            "warning":    warning,
        }, skipped

    # ── LEG PRICING ───────────────────────────────────

    def _price_leg(self, leg: dict) -> Optional[dict]:
        """
        Return a dict with current Greeks for the leg.

        Priority order:
          1. Greeks baked into the leg at fill time (paper_broker writes
             these when OptionsLayer used the real chain).
          2. Live lookup via OptionsChain if we have ticker / strike / exp.
          3. None — caller treats as "un-priceable", skips the leg.
        """
        # 1. If the leg already carries Greeks, use them.
        if any(isinstance(leg.get(k), (int, float)) for k in ("delta", "theta", "vega")):
            return {
                "type":       leg.get("type") or leg.get("option_type"),
                "strike":     leg.get("strike"),
                "expiration": leg.get("expiration"),
                "ticker":     leg.get("ticker"),
                "delta":      leg.get("delta"),
                "theta":      leg.get("theta"),
                "vega":       leg.get("vega"),
                "gamma":      leg.get("gamma"),
            }

        # 2. Live lookup — needs OptionsChain + enough hints.
        if self.chain is None:
            return None
        ticker = leg.get("ticker")
        if ticker:
            try:
                # The chain doesn't have a direct get-by-ticker; pull the
                # exp range around the leg's expiration and match.
                exp_str = leg.get("expiration")
                if not exp_str:
                    return None
                exp = date.fromisoformat(exp_str[:10])
                contract_type = (leg.get("type") or leg.get("option_type") or "").lower()
                strike = float(leg.get("strike") or 0)
                if not (contract_type and strike):
                    return None
                from datetime import timedelta
                chain = self.chain.get_chain(
                    leg.get("underlying") or "SPY",
                    contract_type,
                    exp - timedelta(days=1),
                    exp + timedelta(days=1),
                    strike_min = strike - 0.5,
                    strike_max = strike + 0.5,
                )
                for c in chain:
                    if c["ticker"] == ticker or abs(c["strike"] - strike) < 0.01:
                        return c
            except Exception as e:
                logger.warning(f"PortfolioGreeks: live lookup failed for {ticker}: {e}")
        return None

    # ── HELPERS ───────────────────────────────────────

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception as e:
            logger.warning(f"PortfolioGreeks: data source failed: {e}")
            return default
