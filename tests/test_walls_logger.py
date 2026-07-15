"""tests/test_walls_logger.py -- daily option-wall snapshots (forward data
for the future options-magnet study; docs/MAGNET_STUDY.md)."""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _walls():
    return {"max_pain": 750.0,
            "call_walls": [{"strike": 760.0, "open_interest": 9000}],
            "put_walls": [{"strike": 740.0, "open_interest": 8000}],
            "expiration": "2026-07-17"}


def test_snapshot_appends_once_per_day(tmp_path):
    from learning.walls_logger import already_logged, snapshot
    path = str(tmp_path / "walls.jsonl")
    rec = snapshot(751.8, _walls(), today="2026-07-15", path=path)
    assert rec and rec["max_pain"] == 750.0 and rec["spot"] == 751.8
    assert already_logged("2026-07-15", path)
    assert snapshot(752.0, _walls(), today="2026-07-15", path=path) is None
    lines = open(path).read().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["call_walls"][0]["strike"] == 760.0


def test_snapshot_skips_empty_walls(tmp_path):
    from learning.walls_logger import snapshot
    assert snapshot(751.8, {}, today="2026-07-15",
                    path=str(tmp_path / "w.jsonl")) is None
