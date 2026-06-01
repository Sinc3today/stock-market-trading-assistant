"""
data/options_chain.py -- Real options chain fetcher (Polygon Options Starter+).

Wraps polygon-api-client's SnapshotClient.list_snapshot_options_chain
and normalises each contract into a clean dict the OptionsLayer can use
to pick REAL tradeable strikes instead of theoretical (spot + width)
prices.

Returned contract shape:

    {
        "ticker":      "O:SPY260605C00580000",
        "strike":      580.0,
        "expiration":  "2026-06-05",
        "dte":         20,
        "type":        "call" | "put",
        "mid":         4.20,         # (bid+ask)/2 — None outside market hours
        "iv":          0.143,        # implied volatility (decimal)
        "delta":       0.55,
        "gamma":       0.012,
        "theta":       -0.036,
        "vega":        0.181,
        "open_interest": 12_450,
        "volume":      830,          # day volume, None outside market hours
    }

Caching:
    Contracts are cached for `cache_ttl_seconds` (default 300s) per
    (ticker, expiration_range, type) key to stay polite to Polygon
    even when the OptionsLayer asks for the same chain repeatedly
    in one brief-build cycle.

Notes:
    - Saturdays / pre-market: day.volume and last_quote are None.
      mid_price falls back to None; callers should treat None as
      "no live price" and either skip the strike or use last_close.
    - Requires Polygon Stocks Starter + Options Starter (or higher).
      Returns [] cleanly if the API rejects (NOT_AUTHORIZED).
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger


DEFAULT_CACHE_TTL = 300   # seconds; 5 min within a single brief-build cycle


class OptionsChain:
    """
    Fetches and normalises Polygon options-chain snapshots.

    All fetches are cached in-process. The OptionsLayer creates a
    single instance and reuses it across legs.
    """

    def __init__(
        self,
        polygon_client       = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL,
    ):
        # polygon_client is unused — we instantiate our own RESTClient
        # so callers don't have to know about the SDK. Accept it for
        # interface symmetry with the rest of the module family.
        self._cache: dict = {}
        self._cache_ttl = cache_ttl_seconds
        self._client = None     # lazy-init to keep import cheap

    # ── PUBLIC API ────────────────────────────────────

    def get_chain(
        self,
        ticker:          str,
        contract_type:   str,                 # "call" | "put"
        min_expiration:  date,
        max_expiration:  date,
        strike_min:      float | None = None,
        strike_max:      float | None = None,
        limit:           int  = 50,
    ) -> list[dict]:
        """
        Fetch normalised options snapshot contracts. Returns [] on any
        API failure (caller treats empty as "fall back to theoretical").
        """
        key = (ticker, contract_type, min_expiration.isoformat(),
                max_expiration.isoformat(),
                None if strike_min is None else round(strike_min, 2),
                None if strike_max is None else round(strike_max, 2),
                limit)
        cached = self._cache.get(key)
        if cached and (datetime.now() - cached["at"]).total_seconds() < self._cache_ttl:
            return cached["data"]

        raw = self._fetch_raw(ticker, contract_type,
                               min_expiration, max_expiration,
                               strike_min, strike_max, limit)
        normalised = [self._normalise(c) for c in raw]
        normalised = [c for c in normalised if c]   # drop None entries

        self._cache[key] = {"at": datetime.now(), "data": normalised}
        return normalised

    def find_iron_condor(
        self,
        ticker:           str,
        spot:             float,
        dte_target:       int,
        short_delta:      float = 0.20,
        wing_width:       float = 5.0,
        dte_tolerance:    int = 7,
    ) -> Optional[dict]:
        """
        Pick four real strikes for an iron condor at approximately
        `short_delta` on each short leg with `wing_width` long protection.

        Returns:
            {
              "short_call": <contract>,
              "long_call":  <contract>,
              "short_put":  <contract>,
              "long_put":   <contract>,
              "net_credit": float,     (short premium received minus long paid)
              "max_loss":   float,     (wing_width minus net_credit, × 100)
              "dte":        int,
              "expiration": "YYYY-MM-DD",
            }
            or None if the chain is unavailable / can't satisfy constraints.
        """
        min_exp = date.today() + timedelta(days=max(1, dte_target - dte_tolerance))
        max_exp = date.today() + timedelta(days=dte_target + dte_tolerance)

        calls = self.get_chain(ticker, "call", min_exp, max_exp,
                                strike_min=spot, strike_max=spot * 1.10)
        puts  = self.get_chain(ticker, "put",  min_exp, max_exp,
                                strike_min=spot * 0.90, strike_max=spot)
        if not calls or not puts:
            return None

        # Pick the call with delta closest to target_short_delta
        short_call = min(
            calls,
            key=lambda c: abs((c.get("delta") or 0) - short_delta),
        )
        long_call = self._strike_above(calls, short_call["strike"] + wing_width)

        # Puts have negative delta — match magnitudes
        short_put = min(
            puts,
            key=lambda c: abs(abs(c.get("delta") or 0) - short_delta),
        )
        long_put = self._strike_below(puts, short_put["strike"] - wing_width)

        if not all([short_call, long_call, short_put, long_put]):
            return None

        net_credit = (
            self._safe_mid(short_call) + self._safe_mid(short_put)
            - self._safe_mid(long_call) - self._safe_mid(long_put)
        )
        max_loss = max((wing_width - max(net_credit, 0)) * 100, 0)

        return {
            "short_call": short_call,
            "long_call":  long_call,
            "short_put":  short_put,
            "long_put":   long_put,
            "net_credit": round(net_credit, 3),
            "max_profit": round(net_credit * 100, 2),
            "max_loss":   round(max_loss, 2),
            "dte":        short_call["dte"],
            "expiration": short_call["expiration"],
        }

    def find_vertical_spread(
        self,
        ticker:           str,
        direction:        str,            # "bullish" | "bearish"
        kind:             str,            # "debit" | "credit"
        spot:             float,
        dte_target:       int,
        width:            float = 5.0,
        dte_tolerance:    int = 7,
    ) -> Optional[dict]:
        """
        Pick two real strikes for a vertical spread.

        debit  + bullish -> bull call spread (buy ATM call, sell OTM call)
        debit  + bearish -> bear put spread  (buy ATM put,  sell OTM put)
        credit + bullish -> bull put spread  (sell ATM put, buy further-OTM put)
        credit + bearish -> bear call spread (sell ATM call, buy further-OTM call)
        """
        # Strategy → contract type:
        #   debit  + bullish  = bull CALL debit  (buy calls)
        #   debit  + bearish  = bear PUT  debit  (buy puts)
        #   credit + bullish  = bull PUT  credit (sell puts)  ← puts!
        #   credit + bearish  = bear CALL credit (sell calls) ← calls!
        if (direction == "bullish" and kind == "debit") or \
           (direction == "bearish" and kind == "credit"):
            contract_type = "call"
        else:
            contract_type = "put"

        # Strike layout:
        #   debit  + bullish (BCS): buy ATM call,  sell call at spot + width
        #   debit  + bearish (BPS): buy ATM put,   sell put  at spot - width
        #   credit + bullish (BPS): sell ATM put,  buy  put  at spot - width
        #   credit + bearish (BCS): sell ATM call, buy  call at spot + width
        if kind == "debit":
            buy_strike  = spot
            sell_strike = spot + width if direction == "bullish" else spot - width
        else:   # credit
            sell_strike = spot
            buy_strike  = spot - width if direction == "bullish" else spot + width

        min_exp = date.today() + timedelta(days=max(1, dte_target - dte_tolerance))
        max_exp = date.today() + timedelta(days=dte_target + dte_tolerance)

        chain = self.get_chain(
            ticker, contract_type, min_exp, max_exp,
            strike_min=min(buy_strike, sell_strike) - 2,
            strike_max=max(buy_strike, sell_strike) + 2,
        )
        if not chain:
            return None

        buy_leg  = self._closest_strike(chain, buy_strike)
        sell_leg = self._closest_strike(chain, sell_strike)
        if not buy_leg or not sell_leg or buy_leg["ticker"] == sell_leg["ticker"]:
            return None

        buy_mid  = self._safe_mid(buy_leg)
        sell_mid = self._safe_mid(sell_leg)
        if kind == "debit":
            net_cost  = buy_mid - sell_mid
            max_loss  = round(net_cost * 100, 2)
            max_profit = round((abs(sell_leg["strike"] - buy_leg["strike"]) - net_cost) * 100, 2)
        else:  # credit
            net_credit = sell_mid - buy_mid
            max_profit = round(net_credit * 100, 2)
            max_loss   = round((abs(sell_leg["strike"] - buy_leg["strike"]) - max(net_credit, 0)) * 100, 2)

        return {
            "buy_leg":    buy_leg,
            "sell_leg":   sell_leg,
            "max_profit": max_profit,
            "max_loss":   max_loss,
            "dte":        buy_leg["dte"],
            "expiration": buy_leg["expiration"],
        }

    # ── INTERNAL HELPERS ──────────────────────────────

    @staticmethod
    def _safe_mid(contract: dict) -> float:
        """Per-share mark, or 0.0 when no price is available.

        Prefers the quote midpoint (`mid`); falls back to `mark` (day close /
        vwap) so real-chain pricing still works on a plan whose snapshot has no
        bid/ask. See memory: reference-polygon-snapshot-no-quotes.
        """
        for key in ("mid", "mark"):
            m = contract.get(key)
            if isinstance(m, (int, float)):
                return float(m)
        return 0.0

    @staticmethod
    def _closest_strike(chain: list[dict], target: float) -> Optional[dict]:
        if not chain:
            return None
        return min(chain, key=lambda c: abs(c["strike"] - target))

    @staticmethod
    def _strike_above(chain: list[dict], target: float) -> Optional[dict]:
        above = [c for c in chain if c["strike"] >= target]
        return min(above, key=lambda c: c["strike"]) if above else None

    @staticmethod
    def _strike_below(chain: list[dict], target: float) -> Optional[dict]:
        below = [c for c in chain if c["strike"] <= target]
        return max(below, key=lambda c: c["strike"]) if below else None

    # ── FETCH + NORMALISE ─────────────────────────────

    def _ensure_client(self):
        if self._client is None:
            from polygon import RESTClient
            if not config.POLYGON_API_KEY:
                logger.warning("OptionsChain: POLYGON_API_KEY missing")
                return False
            self._client = RESTClient(api_key=config.POLYGON_API_KEY)
        return True

    def _fetch_raw(
        self,
        ticker:         str,
        contract_type:  str,
        min_expiration: date,
        max_expiration: date,
        strike_min:     float | None,
        strike_max:     float | None,
        limit:          int,
    ) -> list:
        if not self._ensure_client():
            return []
        params = {
            "expiration_date.gte": min_expiration.isoformat(),
            "expiration_date.lte": max_expiration.isoformat(),
            "contract_type":       contract_type,
            "limit":               limit,
        }
        if strike_min is not None:
            params["strike_price.gte"] = strike_min
        if strike_max is not None:
            params["strike_price.lte"] = strike_max
        try:
            return list(self._client.list_snapshot_options_chain(
                underlying_asset=ticker, params=params,
            ))
        except Exception as e:
            logger.warning(
                f"OptionsChain: {ticker} {contract_type} fetch failed: {e}"
            )
            return []

    @staticmethod
    def _normalise(snapshot) -> Optional[dict]:
        """
        Convert a polygon SDK SnapshotResponse to our flat dict.
        Returns None if the snapshot is malformed.
        """
        try:
            d = snapshot.details
            g = snapshot.greeks
            q = snapshot.last_quote
            day = snapshot.day
        except AttributeError:
            return None

        # Mid from last_quote (bid+ask)/2 when both present
        bid = getattr(q, "bid", None) if q else None
        ask = getattr(q, "ask", None) if q else None
        mid = None
        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask > 0:
            mid = round((bid + ask) / 2, 3)

        # Mark: a usable per-share price even when this plan's snapshot carries
        # no quote (last_quote None → mid None). Prefer the quote midpoint, then
        # fall back to the day's close, then vwap. See memory:
        # reference-polygon-snapshot-no-quotes.
        mark = mid
        if mark is None and day is not None:
            for cand in (getattr(day, "close", None), getattr(day, "vwap", None)):
                if isinstance(cand, (int, float)) and cand > 0:
                    mark = round(float(cand), 3)
                    break

        try:
            exp_str = d.expiration_date
            exp_date = date.fromisoformat(exp_str if isinstance(exp_str, str) else exp_str.isoformat())
            dte = (exp_date - date.today()).days
        except (ValueError, AttributeError):
            return None

        return {
            "ticker":        d.ticker,
            "strike":        float(d.strike_price),
            "expiration":    exp_date.isoformat(),
            "dte":           dte,
            "type":          d.contract_type,
            "mid":           mid,
            "mark":          mark,
            "bid":           bid,
            "ask":           ask,
            "iv":            getattr(snapshot, "implied_volatility", None),
            "delta":         getattr(g, "delta", None) if g else None,
            "gamma":         getattr(g, "gamma", None) if g else None,
            "theta":         getattr(g, "theta", None) if g else None,
            "vega":          getattr(g, "vega",  None) if g else None,
            "open_interest": getattr(snapshot, "open_interest", None),
            "volume":        getattr(day, "volume", None) if day else None,
        }
