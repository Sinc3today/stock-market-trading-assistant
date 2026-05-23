"""
backtests/refresh_all_history.py -- One-shot daily history refresh.

Pulls free daily data from three sources:
  - yfinance (SPY + family + sector ETFs + bonds + commodities)
  - CBOE (VIX, VIX9D, VIX3M, VIX6M, VVIX) via the same CSV pattern data/vix_client
    already uses for the daily VIX fallback
  - FRED (10Y / 2Y / 3M yields, fed funds) via the existing FRED API key

All outputs are CSVs under `backtests/` with standardized columns:
  - OHLC frames: date, open, high, low, close, volume
  - Single-series (VIX / yields): date, value

The existing `backtests/spy_history.csv` is NOT overwritten — yfinance writes
to `spy_history_yf.csv`. Phase 2+ harnesses opt into the deeper history;
existing harnesses keep their current source.

Run:
    python -m backtests.refresh_all_history
    python -m backtests.refresh_all_history --skip-fred   # if FRED key unavailable
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from typing import Iterable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config

OUT_DIR  = os.path.join(os.path.dirname(__file__))  # backtests/
OHLC_COLS = ["open", "high", "low", "close", "volume"]

# What we fetch — declared up front so tests can verify shape without network.
SPY_LIKE_TICKERS = [
    # Core
    "SPY", "QQQ", "IWM",
    # Sector ETFs (XLK matters most per recent KB; full set anyway)
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # Bonds / rates / FX
    "TLT", "IEF", "HYG", "UUP",
    # Commodities (sometimes correlated)
    "GLD", "USO",
]

# CBOE-hosted CSVs (same source pattern as data/vix_client's VIX fallback).
# Endpoints follow https://cdn.cboe.com/api/global/us_indices/daily_prices/<name>_History.csv
CBOE_VIX_FAMILY = {
    "VIX":   "VIX_History.csv",
    "VIX9D": "VIX9D_History.csv",
    "VIX3M": "VIX3M_History.csv",
    "VIX6M": "VIX6M_History.csv",
    "VVIX":  "VVIX_History.csv",
}
CBOE_URL_PREFIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/"

# FRED series IDs.
FRED_SERIES = {
    "DGS10": "10y_yield",   # 10-year Treasury constant maturity
    "DGS2":  "2y_yield",
    "DGS3MO": "3m_yield",
    "DFF":   "fed_funds",
}


# ── Normalizers ──────────────────────────────────────────────────────────────

def normalize_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance returns Open/High/Low/Close/Volume; standardize to lowercase
    and index name 'date'."""
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    out = out[[c for c in OHLC_COLS if c in out.columns]]
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    return out


def normalize_series_frame(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """A single-series source (VIX, yield) → date, value."""
    out = df.copy()
    if value_col in out.columns:
        out = out[[value_col]]
        out.columns = ["value"]
    else:
        # First non-index column.
        c = out.columns[0]
        out = out[[c]]
        out.columns = ["value"]
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    return out


# ── Fetchers ─────────────────────────────────────────────────────────────────

def fetch_yfinance_ohlc(ticker: str, start: str = "1993-01-01") -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — pip install yfinance==0.2.50")
        return None
    try:
        df = yf.download(ticker, start=start, progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        # yfinance sometimes returns multi-index columns; flatten if so.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return normalize_ohlc_frame(df)
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {e}")
        return None


def fetch_cboe_csv(filename: str) -> pd.DataFrame | None:
    import requests
    url = CBOE_URL_PREFIX + filename
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        # CBOE format: DATE,OPEN,HIGH,LOW,CLOSE — we want CLOSE as value.
        df.columns = [c.strip().lower() for c in df.columns]
        date_col = next((c for c in df.columns if c in ("date", "trade date")), df.columns[0])
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col]).set_index(date_col)
        # Prefer CLOSE; fall back to first numeric column.
        value_col = "close" if "close" in df.columns else df.select_dtypes(include="number").columns[0]
        return normalize_series_frame(df.rename(columns={value_col: "value"}), value_col="value")
    except Exception as e:
        logger.warning(f"CBOE fetch failed for {filename}: {e}")
        return None


def fetch_fred_series(series_id: str) -> pd.DataFrame | None:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning(f"FRED_API_KEY not set — skipping {series_id}")
        return None
    import requests
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json"}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("observations", [])
        if not data:
            return None
        rows = []
        for o in data:
            try:
                v = float(o["value"])
            except (ValueError, TypeError):
                continue
            rows.append({"date": pd.Timestamp(o["date"]), "value": v})
        if not rows:
            return None
        return pd.DataFrame(rows).set_index("date").rename_axis("date")
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
        return None


# ── Writers ──────────────────────────────────────────────────────────────────

def write_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path)
    logger.info(f"wrote {path} ({len(df)} rows)")


# ── Orchestrator ─────────────────────────────────────────────────────────────

def refresh_all(*, skip_yf: bool = False, skip_cboe: bool = False,
                skip_fred: bool = False) -> dict:
    counts: dict[str, int] = {}

    if not skip_yf:
        for ticker in SPY_LIKE_TICKERS:
            df = fetch_yfinance_ohlc(ticker)
            if df is not None:
                # SPY gets a _yf suffix so we don't clobber the existing
                # spy_history.csv that the live backtest expects.
                out_name = f"{ticker.lower()}_history{'_yf' if ticker == 'SPY' else ''}.csv"
                write_csv(df, os.path.join(OUT_DIR, out_name))
                counts[ticker] = len(df)
            time.sleep(0.5)   # be polite to Yahoo

    if not skip_cboe:
        for name, fn in CBOE_VIX_FAMILY.items():
            df = fetch_cboe_csv(fn)
            if df is not None:
                write_csv(df, os.path.join(OUT_DIR, f"{name.lower()}_history.csv"))
                counts[name] = len(df)
            time.sleep(0.5)

    if not skip_fred:
        for series_id, nice in FRED_SERIES.items():
            df = fetch_fred_series(series_id)
            if df is not None:
                write_csv(df, os.path.join(OUT_DIR, f"{nice}_history.csv"))
                counts[series_id] = len(df)
            time.sleep(0.5)

    return counts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skip-yf",   action="store_true")
    p.add_argument("--skip-cboe", action="store_true")
    p.add_argument("--skip-fred", action="store_true")
    args = p.parse_args()
    counts = refresh_all(skip_yf=args.skip_yf, skip_cboe=args.skip_cboe, skip_fred=args.skip_fred)
    print("\nrefresh summary:")
    for k, n in counts.items():
        print(f"  {k:<8s} {n:>6,d} rows")


if __name__ == "__main__":
    main()
