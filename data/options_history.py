"""
data/options_history.py -- Historical option price aggregates (Polygon paid).

OptionsChain (the live snapshot) can't backtest -- it only knows *now*. This
module fetches HISTORICAL option OHLCV for a specific contract via Polygon's
list_aggs, which the paid Options Starter tier provides:

    - daily bars over a contract's life, and
    - intraday (e.g. 5-min) bars, INCLUDING the 0DTE expiry session.

That unlocks real-priced backtests for the short-DTE tracks (0DTE/1DTE) and
lets us replace / sanity-check the Black-Scholes marks the daily realistic
backtest currently uses (BS is unreliable for 0DTE near expiry).

Pure ticker construction is separate + testable; the network fetch returns an
empty DataFrame on any failure so callers degrade gracefully.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "backtests", ".cache", "options")
_COLS = ["open", "high", "low", "close", "volume"]


def _cache_path(contract: str, multiplier: int, timespan: str,
                from_s: str, to_s: str) -> str:
    safe = contract.replace(":", "_")
    return os.path.join(_CACHE_DIR, f"{safe}_{multiplier}{timespan}_{from_s}_{to_s}.parquet")


def option_ticker(underlying: str, expiry: date, cp: str, strike: float) -> str:
    """
    Build the OCC-style Polygon option ticker.

        O:{UND}{YYMMDD}{C|P}{strike*1000, zero-padded to 8 digits}

    e.g. option_ticker("SPY", date(2024,8,16), "C", 550) ->
         "O:SPY240816C00550000"
    """
    cp_c = cp.strip().upper()[0]
    if cp_c not in ("C", "P"):
        raise ValueError(f"cp must be call/put, got {cp!r}")
    strike_int = int(round(float(strike) * 1000))
    return f"O:{underlying.upper()}{expiry:%y%m%d}{cp_c}{strike_int:08d}"


# ── Eastern time: the ONE conversion for Polygon aggregate bars ──────────
#
# get_aggs turns list_aggs epoch-ms into NAIVE UTC. PolygonClient.get_bars, by
# contrast, uses datetime.fromtimestamp and so yields naive HOST-LOCAL time
# (Chicago on this host). A "14:05 entry" read against the wrong convention is
# one to five hours off, silently. Every consumer of these bars converts here.
_EASTERN = "US/Eastern"
RTH_OPEN = "09:30"
RTH_CLOSE = "16:00"
# A quiet leg's last trade stands in for its price this many minutes, no more.
# Past that, the structure is unpriced rather than priced from a stale print.
CARRY_FORWARD_MINUTES = 5


def to_eastern(df: pd.DataFrame) -> pd.DataFrame:
    """Index an aggregate-bar frame in US/Eastern (tz-aware).

    Naive indexes are taken as UTC, which is what get_aggs produces. DST is
    handled by the zone, not a fixed offset: 13:30 UTC is 09:30 ET in summer,
    14:30 UTC is 09:30 ET in winter.
    """
    if df is None or df.empty:
        return df
    idx = pd.DatetimeIndex(df.index)
    out = df.copy()
    out.index = (idx.tz_localize("UTC").tz_convert(_EASTERN) if idx.tz is None
                 else idx.tz_convert(_EASTERN))
    return out


def session_grid(day) -> pd.DatetimeIndex:
    """Every regular-session minute of `day`, 09:30..16:00 ET inclusive."""
    start = pd.Timestamp(f"{day.isoformat()} {RTH_OPEN}").tz_localize(_EASTERN)
    end = pd.Timestamp(f"{day.isoformat()} {RTH_CLOSE}").tz_localize(_EASTERN)
    return pd.date_range(start, end, freq="1min")


def value_at(series: pd.Series, ts) -> float | None:
    """The structure's value at the minute containing `ts`, or None.

    Never looks forward, and never reaches further back than the carry-forward
    already applied per leg — a second look-back here would double it.
    A naive `ts` is read as Eastern.
    """
    if series is None or len(series) == 0:
        return None
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize(_EASTERN) if ts.tzinfo is None else ts.tz_convert(_EASTERN)
    ts = ts.floor("min")
    if ts not in series.index:
        return None
    v = series.loc[ts]
    return None if pd.isna(v) else float(v)


class OptionsHistory:
    """Historical option aggregates via Polygon list_aggs (paid tier)."""

    def __init__(self, client=None, api_key: str | None = None):
        self._client  = client
        self._api_key = api_key or config.POLYGON_API_KEY

    def _ensure_client(self):
        if self._client is None:
            from polygon import RESTClient
            self._client = RESTClient(self._api_key)
        return self._client

    def get_aggs(self, contract: str, multiplier: int, timespan: str,
                 from_date, to_date, limit: int = 50000,
                 use_cache: bool = True) -> pd.DataFrame:
        """
        Fetch OHLCV bars for one option contract. timespan: 'day' | 'minute'.
        Returns a DataFrame indexed by timestamp with open/high/low/close/
        volume, or an empty DataFrame on any failure.

        Cached to parquet (use_cache) so a multi-year backtest doesn't re-hit
        the API for thousands of contracts. EMPTY results are cached too — an
        illiquid strike with no bars shouldn't re-fetch on every run.
        """
        f = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        t = to_date.isoformat()   if hasattr(to_date, "isoformat")   else str(to_date)
        path = _cache_path(contract, multiplier, timespan, f, t)

        if use_cache and os.path.exists(path):
            try:
                return pd.read_parquet(path)
            except Exception as e:
                logger.warning(f"OptionsHistory: cache read failed ({path}): {e}")

        try:
            bars = list(self._ensure_client().list_aggs(
                contract, multiplier, timespan, f, t, limit=limit,
            ))
        except Exception as e:
            logger.warning(f"OptionsHistory: list_aggs failed for {contract}: {e}")
            return pd.DataFrame(columns=_COLS)

        if not bars:
            df = pd.DataFrame(columns=_COLS)
            df.index.name = "timestamp"
        else:
            rows = [{
                "timestamp": pd.to_datetime(getattr(b, "timestamp", None), unit="ms"),
                "open":   getattr(b, "open", None),
                "high":   getattr(b, "high", None),
                "low":    getattr(b, "low", None),
                "close":  getattr(b, "close", None),
                "volume": getattr(b, "volume", None),
            } for b in bars]
            df = pd.DataFrame(rows).set_index("timestamp").sort_index()

        if use_cache:
            try:
                os.makedirs(_CACHE_DIR, exist_ok=True)
                df.to_parquet(path)
            except Exception as e:
                logger.warning(f"OptionsHistory: cache write failed ({path}): {e}")
        return df

    def leg_close(self, underlying: str, expiry: date, cp: str, strike: float,
                  on: date) -> float | None:
        """Real closing price of one option leg on a given date (daily bar).
        Returns None if no bar exists."""
        contract = option_ticker(underlying, expiry, cp, strike)
        df = self.get_aggs(contract, 1, "day", on, on)
        if df.empty:
            return None
        return float(df["close"].iloc[-1])

    def structure_minutes(self, underlying: str, day, legs: list[dict],
                          use_cache: bool = True) -> pd.Series:
        """Per-share value of a multi-leg structure at every session minute.

        Long legs add, short legs subtract. Each leg's real last trade is carried
        forward at most CARRY_FORWARD_MINUTES; if ANY leg has no bars at all the
        whole series is NaN, because a structure priced from some of its legs
        reports a value that never existed.

        Leg direction is matched case-insensitively (the journal holds both
        casings) and an unrecognised action raises rather than being guessed.
        """
        grid = session_grid(day)
        total = pd.Series(0.0, index=grid)
        for leg in legs:
            leg = leg or {}
            action = str(leg.get("action") or "").strip().upper()
            if action not in ("BUY", "SELL"):
                raise ValueError(f"unrecognised leg action {leg.get('action')!r} in leg {leg!r}")
            cp = leg.get("option_type") or leg.get("type")
            if not cp:
                raise ValueError(f"leg has no option type: {leg!r}")
            exp = leg.get("expiration") or leg.get("expiry")
            expiry = exp if isinstance(exp, date) else date.fromisoformat(str(exp)[:10])
            contract = option_ticker(underlying, expiry, str(cp), float(leg["strike"]))
            bars = to_eastern(self.get_aggs(contract, 1, "minute", day, day,
                                            use_cache=use_cache))
            if bars is None or bars.empty:
                return pd.Series(float("nan"), index=grid)
            close = bars["close"].astype(float)
            close = close[~close.index.duplicated(keep="last")]
            px = close.reindex(grid).ffill(limit=CARRY_FORWARD_MINUTES)
            total = total + (px if action == "BUY" else -px)
        return total
