import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config


@pytest.mark.integration
def test_backfill_then_rollup_on_real_april_2024(tmp_path, monkeypatch):
    """Real cached SPY 5-min parquet (backtests/.cache) → backfill → rollup."""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    from datetime import date
    from backtests.router_track_backfill import backfill
    from backtests.router_track_rollup import load_jsonl_dir, rollup_to_parquet
    import pandas as pd

    n = backfill(date(2024, 4, 1), date(2024, 4, 5))
    assert n >= 1                                   # April 2024 has cached data + qualifying setups
    track_dir = os.path.join(str(tmp_path), "learning", "router_track")
    out = str(tmp_path / "rollup.parquet")
    rollup_to_parquet(load_jsonl_dir(track_dir), out)
    df = pd.read_parquet(out)
    assert len(df) == n
    assert df["source"].eq("backfill").all()
