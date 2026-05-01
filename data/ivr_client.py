"""
data/ivr_client.py — IV Rank (IVR) Client

Computes IV Rank for any ticker — specifically SPY for the daily strategy.

Formula:
    IVR = (current_IV - 52w_low_IV) / (52w_high_IV - 52w_low_IV) × 100

Where current_IV is the 30-day implied volatility estimated from the
ATM straddle (nearest-expiry call + put at the strike closest to spot).

Data source:  Polygon.io options chain (requires Options add-on, ~$29/mo)
Fallback:     VIX proxy — VIX is SPY's 30-day IV model output,
              so IVR_proxy = (VIX - 52w_low_VIX) / (52w_high_VIX - 52w_low_VIX) × 100
              This is not perfect but directionally accurate and free.

The fallback is used automatically when the options chain is unavailable.
The regime detector cares about IVR bands (< 30, 30–50, > 50), not precision,
so the VIX proxy is good enough to drive strategy selection.

Usage:
    from data.ivr_client import IVRClient
    client = IVRClient(polygon_client, vix_client)
    ivr = client.get_iv_rank("SPY")   # 0–100 float

Run standalone:
    python -m data.ivr_client
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta, date
from typing import Optional

from loguru import logger

# ── Cache TTL ────────────────────────────────────────────────
_CACHE_TTL_SECONDS = 300
_cache: dict = {}


class IVRClient:
    """
    Computes IV Rank for a ticker.

    Inject polygon_client and vix_client at construction.
    If polygon_client is None, falls back to VIX proxy automatically.
    """

    # IVR history window — 252 trading days ≈ 1 year
    HISTORY_DAYS = 365

    def __init__(self, polygon_client=None, vix_client=None):
        self.polygon  = polygon_client   # data.polygon_client.PolygonClient
        self.vix      = vix_client       # data.vix_client.VIXClient
        logger.info("IVRClient initialized")

    # ─────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────

    def get_iv_rank(self, ticker: str = "SPY") -> float:
        """
        Return IV Rank for ticker (0–100).

        Tries options-chain method first (precise).
        Falls back to VIX proxy (directionally accurate, free).
        Returns 30.0 as a neutral fallback if all sources fail.
        """
        # ── Cache ──────────────────────────────────────────────
        cached = _cache.get(ticker)
        if cached:
            age = (datetime.now() - cached["fetched_at"]).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                logger.debug(f"IVR {ticker} from cache: {cached['ivr']:.1f}")
                return cached["ivr"]

        # ── Options chain method ───────────────────────────────
        if self.polygon is not None:
            ivr = self._compute_from_options_chain(ticker)
            if ivr is not None:
                _cache[ticker] = {"ivr": ivr, "fetched_at": datetime.now()}
                return ivr

        # ── VIX proxy method (free fallback) ───────────────────
        if self.vix is not None:
            ivr = self._compute_vix_proxy()
            if ivr is not None:
                logger.info(f"IVR {ticker}: using VIX proxy = {ivr:.1f}")
                _cache[ticker] = {"ivr": ivr, "fetched_at": datetime.now()}
                return ivr

        # ── Neutral fallback ───────────────────────────────────
        logger.warning(f"IVR unavailable for {ticker} — using fallback 30.0")
        return 30.0

    # ─────────────────────────────────────────
    # OPTIONS CHAIN METHOD
    # ─────────────────────────────────────────

    def _compute_from_options_chain(self, ticker: str) -> Optional[float]:
        """
        Estimate current 30-day IV from the ATM straddle.

        Step 1: Get current spot price
        Step 2: Find nearest expiry 25–35 DTE (captures 30-day IV)
        Step 3: Find ATM call + put (strike closest to spot)
        Step 4: iv = average of call IV + put IV
        Step 5: Compare to 52-week IV history stored in rolling log

        Note: Polygon Options requires the Stocks Starter+ plan.
        This will silently fall back to VIX proxy on free plans.
        """
        try:
            from polygon import RESTClient
            client = RESTClient(api_key=self.polygon.client.api_key
                                if hasattr(self.polygon, "client") else None)

            # Step 1: spot price
            spot_df = self.polygon.get_bars(ticker, timeframe="day", limit=1, days_back=5)
            if spot_df is None or len(spot_df) == 0:
                return None
            spot = float(spot_df["close"].iloc[-1])

            # Step 2: find target expiry (25–40 DTE)
            today      = date.today()
            target_dte = (today + timedelta(days=30)).isoformat()
            min_expiry = (today + timedelta(days=25)).isoformat()
            max_expiry = (today + timedelta(days=40)).isoformat()

            # Step 3: fetch ATM call
            call_chain = list(client.list_snapshot_options_chain(
                underlying_asset = ticker,
                contract_type    = "call",
                expiration_date_gte = min_expiry,
                expiration_date_lte = max_expiry,
                strike_price_gte = spot * 0.98,
                strike_price_lte = spot * 1.02,
                limit            = 10,
            ))

            put_chain = list(client.list_snapshot_options_chain(
                underlying_asset = ticker,
                contract_type    = "put",
                expiration_date_gte = min_expiry,
                expiration_date_lte = max_expiry,
                strike_price_gte = spot * 0.98,
                strike_price_lte = spot * 1.02,
                limit            = 10,
            ))

            if not call_chain or not put_chain:
                logger.debug(f"No options chain data for {ticker} — likely free plan")
                return None

            # Step 4: extract IVs
            call_ivs = [
                c.implied_volatility for c in call_chain
                if hasattr(c, "implied_volatility") and c.implied_volatility
            ]
            put_ivs = [
                p.implied_volatility for p in put_chain
                if hasattr(p, "implied_volatility") and p.implied_volatility
            ]

            if not call_ivs and not put_ivs:
                return None

            all_ivs   = call_ivs + put_ivs
            current_iv = sum(all_ivs) / len(all_ivs) * 100  # to percentage

            # Step 5: compute IVR from rolling log
            ivr = self._compute_ivr_from_history(ticker, current_iv)
            logger.info(f"IVR {ticker} from options chain: {ivr:.1f} (IV={current_iv:.1f}%)")
            return ivr

        except Exception as e:
            logger.debug(f"Options chain IVR failed for {ticker}: {e}")
            return None

    def _compute_ivr_from_history(
        self, ticker: str, current_iv: float
    ) -> float:
        """
        Compute IVR by comparing current_iv against the rolling 52-week
        high/low stored in logs/iv_history.json.

        We maintain a simple rolling log of daily IV readings.
        First run will return 50.0 (neutral) until enough history builds.
        """
        import json, os
        history_path = os.path.join("logs", f"iv_history_{ticker}.json")
        os.makedirs("logs", exist_ok=True)

        # Load history
        history: list[dict] = []
        if os.path.exists(history_path):
            try:
                with open(history_path) as f:
                    history = json.load(f)
            except Exception:
                history = []

        # Append today's reading
        today_str = date.today().isoformat()
        history = [h for h in history if h.get("date") != today_str]  # dedupe
        history.append({"date": today_str, "iv": round(current_iv, 2)})

        # Keep 252 trading days
        history = sorted(history, key=lambda h: h["date"])[-365:]
        with open(history_path, "w") as f:
            json.dump(history, f, indent=2)

        if len(history) < 20:
            logger.info(f"IV history for {ticker} only {len(history)} days — returning 50")
            return 50.0

        ivs = [h["iv"] for h in history]
        iv_low  = min(ivs)
        iv_high = max(ivs)
        spread  = iv_high - iv_low

        if spread < 0.5:   # avoid div/0 on flat IV environments
            return 50.0

        ivr = (current_iv - iv_low) / spread * 100
        return round(max(0.0, min(100.0, ivr)), 1)

    # ─────────────────────────────────────────
    # VIX PROXY METHOD (free fallback)
    # ─────────────────────────────────────────

    def _compute_vix_proxy(self) -> Optional[float]:
        """
        Use VIX as a proxy for SPY's 30-day IV.
        VIX is literally derived from SPY options — it IS SPY's IV.
        IVR_proxy = (current_VIX - 52w_low_VIX) / (52w_high_VIX - 52w_low_VIX) × 100
        """
        try:
            current_vix = self.vix.get_current()
            hist        = self.vix.get_history(days=self.HISTORY_DAYS)

            if hist is None or len(hist) < 20:
                return None

            vix_low   = float(hist["close"].min())
            vix_high  = float(hist["close"].max())
            spread    = vix_high - vix_low

            if spread < 0.5:
                return 50.0

            ivr = (current_vix - vix_low) / spread * 100
            return round(max(0.0, min(100.0, ivr)), 1)

        except Exception as e:
            logger.error(f"VIX proxy IVR failed: {e}")
            return None


# ─────────────────────────────────────────
# STANDALONE SMOKE TEST
# ─────────────────────────────────────────

if __name__ == "__main__":
    from data.vix_client import VIXClient

    vix_client = VIXClient()
    ivr_client = IVRClient(polygon_client=None, vix_client=vix_client)

    print("\n── IVR via VIX proxy ──")
    ivr = ivr_client.get_iv_rank("SPY")
    print(f"  SPY IVR = {ivr:.1f}")

    vix = vix_client.get_current()
    hist = vix_client.get_history(days=252)
    if hist is not None:
        print(f"\n── VIX context ──")
        print(f"  Current VIX:  {vix:.2f}")
        print(f"  52w VIX high: {hist['close'].max():.2f}")
        print(f"  52w VIX low:  {hist['close'].min():.2f}")
        print(f"  IVR:          {ivr:.1f}/100")
        if   ivr < 30:  print("  → Low IVR — options are cheap, buy premium")
        elif ivr < 50:  print("  → Moderate IVR — neutral")
        else:           print("  → High IVR — options are expensive, sell premium")
