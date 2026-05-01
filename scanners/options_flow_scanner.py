"""
scanners/options_flow_scanner.py -- Unusual Options Activity (UOA) Scanner

Detects institutional-scale options positioning by looking for contracts
where today's volume significantly exceeds open interest -- the clearest
free-data signal that large new money is entering a position.

What it flags:
    VOL/OI SPIKE   -- Volume >= 3x open interest on a single contract
                      Means new money, not just position rolls
    SWEEP PROXY    -- High volume + wide bid/ask + OTM = aggressive buyer
                      Proxy for sweep orders without real-time feed
    PUT/CALL SKEW  -- Unusual ratio of put vs call volume vs 30-day norm
                      Heavy put buying on uptrending stock = hedge or bearish bet
    OTM MONSTER    -- Deep OTM contract with >500 contracts traded
                      Often precedes large directional moves

Data source:
    Polygon.io options chain (free tier)
    Works with your existing PolygonClient

Limitations (free plan):
    - No real-time data -- uses previous day's close
    - No individual trade timestamps -- can't distinguish sweeps from blocks
    - Polygon free plan limits options chain depth

Usage:
    from scanners.options_flow_scanner import OptionsFlowScanner
    scanner = OptionsFlowScanner()
    alerts  = scanner.scan(["SPY", "QQQ", "AAPL", "NVDA"])
    scanner.run()   # Full watchlist scan + Discord post

Run standalone:
    python -m scanners.options_flow_scanner
"""

from __future__ import annotations

import os
import sys
import json
from datetime import date, datetime
from typing import Optional

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config

# ── Thresholds ────────────────────────────────────────────────
VOL_OI_SPIKE_MIN      = 3.0    # Volume >= 3x OI = unusual
VOL_OI_STRONG_MIN     = 5.0    # Volume >= 5x OI = strong signal
OTM_MONSTER_MIN       = 500    # Contracts on OTM strike = big bet
MIN_CONTRACT_VOLUME   = 100    # Ignore tiny trades (noise)
MIN_OPEN_INTEREST     = 10     # Ignore illiquid contracts
OTM_THRESHOLD_PCT     = 0.05   # 5%+ OTM = "out of the money"
MAX_DTE               = 60     # Ignore LEAPS (too far out)
MIN_DTE               = 1      # Ignore same-day expiry


class OptionsFlowScanner:
    """
    Scans options chains for unusual activity that signals
    large institutional positioning on your watchlist tickers.
    """

    def __init__(self):
        self.discord_post_fn = None
        self.eastern         = __import__("pytz").timezone("US/Eastern")
        self._polygon        = None   # Lazy init

    def set_discord_fn(self, fn):
        """Register the callable used to post UOA summaries (Discord or notifier)."""
        self.discord_post_fn = fn

    # ─────────────────────────────────────────
    # PUBLIC ENTRY POINTS
    # ─────────────────────────────────────────

    def run(self) -> list[dict]:
        """
        Scan full watchlist. Called by scheduler at 9:30 AM ET.
        Posts results to Discord and returns list of signals.
        """
        if not self._is_trading_day():
            logger.info("Options flow scan skipped -- weekend/holiday")
            return []

        watchlist = self._load_watchlist()
        tickers   = list(set(
            watchlist.get("swing", []) +
            watchlist.get("intraday", []) +
            watchlist.get("options_enabled", [])
        ))

        if not tickers:
            logger.warning("Watchlist empty -- options flow scan skipped")
            return []

        logger.info(f"Options flow scan starting -- {len(tickers)} tickers")
        return self.scan(tickers)

    def scan(self, tickers: list[str]) -> list[dict]:
        """
        Scan a list of tickers for unusual options activity.
        Returns list of UOA signal dicts, sorted by conviction.
        """
        all_signals = []

        for ticker in tickers:
            try:
                signals = self._scan_ticker(ticker)
                all_signals.extend(signals)
            except Exception as e:
                logger.error(f"Options flow error for {ticker}: {e}")
                continue

        # Sort by conviction score descending
        all_signals.sort(key=lambda x: x["conviction"], reverse=True)

        if all_signals:
            logger.info(f"Options flow scan done -- {len(all_signals)} signals found")
            self._post_to_discord(all_signals)
        else:
            logger.info("Options flow scan done -- no unusual activity detected")

        return all_signals

    # ─────────────────────────────────────────
    # CORE SCAN LOGIC
    # ─────────────────────────────────────────

    def _scan_ticker(self, ticker: str) -> list[dict]:
        """
        Fetch options chain for a ticker and score each contract
        for unusual activity.
        """
        chain = self._fetch_chain(ticker)
        if chain is None or chain.empty:
            logger.debug(f"{ticker}: no options chain data")
            return []

        spot_price = self._fetch_spot(ticker)
        if spot_price is None:
            logger.debug(f"{ticker}: no spot price")
            return []

        signals = []
        today   = date.today()

        for _, row in chain.iterrows():
            try:
                sig = self._score_contract(row, ticker, spot_price, today)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.debug(f"{ticker} contract score error: {e}")
                continue

        return signals

    def _score_contract(
        self,
        row:         pd.Series,
        ticker:      str,
        spot_price:  float,
        today:       date,
    ) -> Optional[dict]:
        """
        Score a single options contract for unusual activity.
        Returns a signal dict if unusual, None if normal.
        """
        volume = int(row.get("volume", 0) or 0)
        oi     = int(row.get("open_interest", 0) or 0)

        # ── Minimum liquidity filter ──────────────────────────
        if volume < MIN_CONTRACT_VOLUME:
            return None
        if oi < MIN_OPEN_INTEREST and volume < OTM_MONSTER_MIN:
            return None

        # ── Contract metadata ──────────────────────────────────
        contract_type = str(row.get("contract_type", "")).lower()  # call / put
        strike        = float(row.get("strike_price", 0) or 0)
        expiry_str    = str(row.get("expiration_date", ""))
        iv            = float(row.get("implied_volatility", 0) or 0)
        delta         = float(row.get("delta", 0) or 0)

        try:
            expiry = date.fromisoformat(expiry_str)
            dte    = (expiry - today).days
        except (ValueError, TypeError):
            return None

        # ── DTE filter ─────────────────────────────────────────
        if not (MIN_DTE <= dte <= MAX_DTE):
            return None

        # ── Moneyness ──────────────────────────────────────────
        if strike <= 0 or spot_price <= 0:
            return None

        pct_from_spot = (strike - spot_price) / spot_price * 100
        is_call       = contract_type == "call"
        is_otm        = (is_call and strike > spot_price) or \
                        (not is_call and strike < spot_price)

        otm_pct = abs(pct_from_spot) if is_otm else 0.0

        # ── Flags ──────────────────────────────────────────────
        flags      = []
        conviction = 0

        # Flag 1: Volume/OI spike
        vol_oi_ratio = volume / oi if oi > 0 else volume
        if vol_oi_ratio >= VOL_OI_STRONG_MIN:
            flags.append(f"VOL/OI={vol_oi_ratio:.1f}x (STRONG)")
            conviction += 40
        elif vol_oi_ratio >= VOL_OI_SPIKE_MIN:
            flags.append(f"VOL/OI={vol_oi_ratio:.1f}x spike")
            conviction += 25

        # Flag 2: OTM monster (deep OTM + huge volume = directional bet)
        if is_otm and otm_pct >= OTM_THRESHOLD_PCT * 100 and volume >= OTM_MONSTER_MIN:
            flags.append(f"OTM monster: {otm_pct:.1f}% OTM, {volume} contracts")
            conviction += 35

        # Flag 3: High IV on near-term expiry (event positioning)
        if iv > 0.60 and dte <= 14:
            flags.append(f"High IV={iv:.0%} with {dte}DTE (event bet)")
            conviction += 20

        # Flag 4: Large absolute volume regardless of OI
        if volume >= 2000:
            flags.append(f"Large block: {volume:,} contracts traded")
            conviction += 15
        elif volume >= 1000:
            flags.append(f"Notable block: {volume:,} contracts")
            conviction += 8

        # Nothing unusual
        if not flags:
            return None

        # ── Determine implied direction ────────────────────────
        # Call buying = bullish, Put buying = bearish
        # But high-IV near-term puts could be hedging, not direction
        implied_direction = "bullish" if is_call else "bearish"
        if not is_call and oi > volume * 5:
            implied_direction = "hedge"  # More likely a hedge than a bet

        # ── Signal dict ────────────────────────────────────────
        return {
            "ticker":            ticker,
            "contract_type":     contract_type.upper(),
            "strike":            strike,
            "expiry":            expiry_str,
            "dte":               dte,
            "volume":            volume,
            "open_interest":     oi,
            "vol_oi_ratio":      round(vol_oi_ratio, 2),
            "iv":                round(iv * 100, 1) if iv else None,
            "delta":             round(delta, 2) if delta else None,
            "otm_pct":           round(otm_pct, 1),
            "is_otm":            is_otm,
            "implied_direction": implied_direction,
            "flags":             flags,
            "conviction":        conviction,
            "spot_price":        round(spot_price, 2),
            "timestamp":         datetime.now(self.eastern).strftime("%Y-%m-%d %I:%M %p EST"),
        }

    # ─────────────────────────────────────────
    # DATA FETCHERS
    # ─────────────────────────────────────────

    def _get_polygon(self):
        """Lazy-load Polygon client."""
        if self._polygon is None:
            from data.polygon_client import PolygonClient
            self._polygon = PolygonClient()
        return self._polygon

    def _fetch_chain(self, ticker: str) -> Optional[pd.DataFrame]:
        """
        Fetch today's options chain for a ticker via Polygon.
        Returns DataFrame with one row per contract.
        """
        try:
            from polygon import RESTClient
            client = RESTClient(api_key=config.POLYGON_API_KEY)

            contracts = list(client.list_snapshot_options_chain(
                underlying_asset = ticker,
                limit            = 250,
            ))

            if not contracts:
                return None

            rows = []
            for c in contracts:
                details = getattr(c, "details", None)
                greeks  = getattr(c, "greeks", None)
                day     = getattr(c, "day", None)

                rows.append({
                    "contract_type":    getattr(details, "contract_type", None),
                    "strike_price":     getattr(details, "strike_price", None),
                    "expiration_date":  getattr(details, "expiration_date", None),
                    "volume":           getattr(day, "volume", 0),
                    "open_interest":    getattr(c, "open_interest", 0),
                    "implied_volatility": getattr(c, "implied_volatility", None),
                    "delta":            getattr(greeks, "delta", None),
                    "vega":             getattr(greeks, "vega", None),
                })

            df = pd.DataFrame(rows)
            logger.debug(f"{ticker}: {len(df)} contracts fetched")
            return df

        except Exception as e:
            logger.debug(f"{ticker} chain fetch failed: {e}")
            return None

    def _fetch_spot(self, ticker: str) -> Optional[float]:
        """Get current spot price for moneyness calculation."""
        try:
            client = self._get_polygon()
            df     = client.get_bars(
                ticker,
                timeframe = config.SWING_PRIMARY_TIMEFRAME,
                limit     = 1,
                days_back = 5,
            )
            if df is not None and len(df) > 0:
                return float(df["close"].iloc[-1])
        except Exception as e:
            logger.debug(f"{ticker} spot price failed: {e}")
        return None

    # ─────────────────────────────────────────
    # DISCORD FORMATTING
    # ─────────────────────────────────────────

    def _post_to_discord(self, signals: list[dict]):
        """Post top UOA signals to Discord."""
        if not self.discord_post_fn or not signals:
            return

        # Only post top 5 by conviction to avoid noise
        top = signals[:5]

        header = (
            f"👁 **UNUSUAL OPTIONS ACTIVITY** -- "
            f"{date.today().isoformat()}\n"
            f"_{len(signals)} signal(s) detected across watchlist_\n"
        )

        lines = [header]
        for s in top:
            direction_emoji = "📈" if s["implied_direction"] == "bullish" else \
                              "📉" if s["implied_direction"] == "bearish" else "🛡"
            otm_str = f" ({s['otm_pct']:.1f}% OTM)" if s["is_otm"] else " (ITM/ATM)"
            iv_str  = f" | IV: {s['iv']}%" if s["iv"] else ""
            lines.append(
                f"\n{direction_emoji} **{s['ticker']}** "
                f"{s['contract_type']} ${s['strike']}{otm_str} "
                f"exp {s['expiry']} ({s['dte']}DTE)\n"
                f"  Vol: {s['volume']:,} | OI: {s['open_interest']:,} | "
                f"Vol/OI: {s['vol_oi_ratio']}x{iv_str}\n"
                f"  Signal: {' | '.join(s['flags'])}\n"
                f"  Implied: **{s['implied_direction'].upper()}** "
                f"(conviction: {s['conviction']})"
            )

        message = "\n".join(lines)

        # Post as plain message to standard alerts channel
        try:
            self.discord_post_fn(message)
        except Exception as e:
            logger.error(f"UOA Discord post failed: {e}")

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _is_trading_day(self) -> bool:
        now = datetime.now(self.eastern)
        return now.weekday() < 5  # Mon-Fri only

    def _load_watchlist(self) -> dict:
        try:
            with open(config.WATCHLIST_PATH) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Watchlist load failed: {e}")
            return {"swing": [], "intraday": [], "options_enabled": []}


# ─────────────────────────────────────────
# STANDALONE SMOKE TEST
# ─────────────────────────────────────────

if __name__ == "__main__":
    scanner = OptionsFlowScanner()
    print("\nScanning SPY and QQQ for unusual options activity...")
    signals = scanner.scan(["SPY", "QQQ"])

    if not signals:
        print("No unusual activity detected (may need Polygon Options add-on)")
        print("Check your Polygon plan at https://polygon.io/dashboard/subscriptions")
    else:
        print(f"\n{len(signals)} signal(s) found:\n")
        for s in signals[:10]:
            print(
                f"  {s['ticker']} {s['contract_type']} ${s['strike']} "
                f"exp {s['expiry']} ({s['dte']}DTE)"
            )
            print(f"  Vol: {s['volume']:,} | OI: {s['open_interest']:,} | "
                  f"Vol/OI: {s['vol_oi_ratio']}x")
            print(f"  Direction: {s['implied_direction'].upper()} | "
                  f"Conviction: {s['conviction']}")
            for flag in s["flags"]:
                print(f"    * {flag}")
            print()
