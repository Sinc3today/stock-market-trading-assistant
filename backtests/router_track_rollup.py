"""backtests/router_track_rollup.py -- Compact router_track JSONL → one parquet.

Reads every logs/learning/router_track/*.jsonl row and writes a columnar
parquet (counts + distinct reject gates) for the calibration / analysis
tooling. The JSONL remains the lossless source of truth.

CLI:
    python -m backtests.router_track_rollup
"""

from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config

_COLUMNS = ["ts", "date", "source", "strategy", "conviction", "score",
            "direction", "trend", "passed_tier", "n_accepted", "n_rejected",
            "reject_gates"]

_DEFAULT_OUT = os.path.join(os.path.dirname(__file__), ".cache", "router_track",
                            "router_track.parquet")


def load_jsonl_dir(dir_path: str) -> list[dict]:
    """Read every *.jsonl row in dir_path. Corrupt lines are skipped with a
    warning (never aborts the rollup). Missing dir → []."""
    records: list[dict] = []
    for path in sorted(glob.glob(os.path.join(dir_path, "*.jsonl"))):
        with open(path) as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"rollup: skip corrupt line {path}:{i}: {e}")
    return records


def rollup_to_parquet(records: list[dict], out_path: str) -> str:
    """Flatten records → parquet at out_path. Returns out_path."""
    rows = [{
        "ts": r["ts"], "date": r["date"], "source": r["source"],
        "strategy": r["strategy"], "conviction": r["conviction"],
        "score": r["score"], "direction": r.get("direction"),
        "trend": r.get("trend"), "passed_tier": r["passed_tier"],
        "n_accepted": len(r["accepted"]),
        "n_rejected": len(r["rejected"]),
        "reject_gates": ",".join(sorted({x["gate"] for x in r["rejected"]})),
    } for r in records]
    df = pd.DataFrame(rows, columns=_COLUMNS)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_parquet(out_path)
    logger.info(f"rollup: {len(df)} rows → {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Roll up router_track JSONL → parquet")
    parser.add_argument("--in",  dest="in_dir",
                        default=os.path.join(config.LOG_DIR, "learning", "router_track"))
    parser.add_argument("--out", default=_DEFAULT_OUT)
    args = parser.parse_args()
    rollup_to_parquet(load_jsonl_dir(args.in_dir), args.out)
