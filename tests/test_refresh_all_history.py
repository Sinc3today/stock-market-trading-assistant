"""Phase 1: refresh_all_history is a one-shot script that writes daily CSVs.

We unit-test the *shape* — that the writer functions emit standardized
columns and the script lives where we expect — without making network calls.
Live integration is a manual `python -m backtests.refresh_all_history` run.
"""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from backtests.refresh_all_history import (
    OUT_DIR, OHLC_COLS, normalize_ohlc_frame, normalize_series_frame,
    SPY_LIKE_TICKERS, CBOE_VIX_FAMILY, FRED_SERIES,
)


def test_targets_are_declared():
    # The script declares which tickers/series it fetches. Sanity-check shape.
    assert "SPY" in SPY_LIKE_TICKERS
    assert "QQQ" in SPY_LIKE_TICKERS
    assert "XLK" in SPY_LIKE_TICKERS
    assert "TLT" in SPY_LIKE_TICKERS
    assert "VIX" in CBOE_VIX_FAMILY
    assert "VVIX" in CBOE_VIX_FAMILY
    assert "DGS10" in FRED_SERIES        # 10Y yield
    assert "DGS2"  in FRED_SERIES        # 2Y yield


def test_normalize_ohlc_frame_standardizes_columns():
    raw = pd.DataFrame({
        "Open": [100.0], "High": [102.0], "Low": [99.0],
        "Close": [101.0], "Volume": [1000000],
    }, index=pd.to_datetime(["2025-01-02"]))
    out = normalize_ohlc_frame(raw)
    assert list(out.columns) == OHLC_COLS
    assert out.index.name == "date"
    assert out.iloc[0]["open"] == 100.0


def test_normalize_series_frame_returns_date_value():
    raw = pd.DataFrame({"VALUE": [17.5, 18.0]},
                       index=pd.to_datetime(["2025-01-02", "2025-01-03"]))
    out = normalize_series_frame(raw, value_col="VALUE")
    assert list(out.columns) == ["value"]
    assert out.index.name == "date"
    assert out.iloc[0]["value"] == 17.5


def test_out_dir_is_under_backtests():
    # Files live alongside spy_history.csv; doesn't touch the existing file.
    assert OUT_DIR.endswith("backtests")
