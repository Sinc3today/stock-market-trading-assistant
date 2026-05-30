import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtests.router_track_rollup import load_jsonl_dir, rollup_to_parquet


def _row(accepted, rejected_gates):
    return {
        "ts": "2024-07-15T09:45:00-04:00", "date": "2024-07-15", "source": "backfill",
        "strategy": "iron_condor", "conviction": "high", "score": 70,
        "direction": "neutral", "trend": "range-bound", "passed_tier": True,
        "accepted": accepted,
        "rejected": [{"dte_bucket": "x", "gate": g, "detail": "d"} for g in rejected_gates],
    }


def test_load_jsonl_dir_reads_multiple_files_and_skips_corrupt(tmp_path, caplog):
    import json
    (tmp_path / "a.jsonl").write_text(json.dumps(_row(["0DTE"], [])) + "\n")
    (tmp_path / "b.jsonl").write_text(
        json.dumps(_row([], ["dte"])) + "\n" + "{not valid json\n")
    records = load_jsonl_dir(str(tmp_path))
    assert len(records) == 2          # 2 valid rows; the corrupt line skipped


def test_rollup_to_parquet_columns_and_flattening(tmp_path):
    records = [_row(["0DTE"], ["dte", "dedup"]), _row([], [])]
    out = str(tmp_path / "rollup.parquet")
    rollup_to_parquet(records, out)
    df = pd.read_parquet(out)
    assert list(df.columns) == [
        "ts", "date", "source", "strategy", "conviction", "score",
        "direction", "trend", "passed_tier", "n_accepted", "n_rejected", "reject_gates"]
    assert df.iloc[0]["n_accepted"] == 1
    assert df.iloc[0]["n_rejected"] == 2
    assert df.iloc[0]["reject_gates"] == "dedup,dte"     # sorted, distinct, comma-joined
    assert df.iloc[1]["reject_gates"] == ""


def test_rollup_empty_dir_writes_schema_only_parquet(tmp_path):
    records = load_jsonl_dir(str(tmp_path))             # empty dir → []
    out = str(tmp_path / "empty.parquet")
    rollup_to_parquet(records, out)
    df = pd.read_parquet(out)
    assert len(df) == 0
    assert "reject_gates" in df.columns
