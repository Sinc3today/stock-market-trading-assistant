import json
import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from signals.spy_options_engine import SPYSetup
from learning.router_tracker import build_trace_record, write_trace

ET = pytz.timezone("US/Eastern")


def _setup(**kw):
    base = dict(strategy="iron_condor", conviction="high", timeframe="intraday",
                score=72, reasons=["a"], direction="neutral", trend="range-bound")
    base.update(kw)
    return SPYSetup(**base)


def _trace():
    return {
        "passed_tier": True,
        "candidate_buckets": ["0DTE"],
        "accepted": [{"dte_bucket": "0DTE"}],
        "rejected": [{"dte_bucket": "1-3DTE", "gate": "dte", "detail": "morning ..."}],
    }


def test_build_trace_record_shape():
    now = ET.localize(datetime(2026, 5, 30, 9, 16))
    rec = build_trace_record(_setup(), _trace(), now, source="live")
    assert rec["ts"] == now.isoformat()
    assert rec["date"] == "2026-05-30"
    assert rec["source"] == "live"
    assert rec["strategy"] == "iron_condor"
    assert rec["conviction"] == "high"
    assert rec["score"] == 72
    assert rec["direction"] == "neutral"
    assert rec["trend"] == "range-bound"
    assert rec["passed_tier"] is True
    assert rec["accepted"] == ["0DTE"]
    assert rec["rejected"] == [{"dte_bucket": "1-3DTE", "gate": "dte", "detail": "morning ..."}]


def test_build_trace_record_handles_none_direction_and_trend():
    now = ET.localize(datetime(2026, 5, 30, 9, 16))
    rec = build_trace_record(_setup(direction=None, trend=None), _trace(), now, source="backfill")
    assert rec["direction"] is None
    assert rec["trend"] is None
    assert rec["source"] == "backfill"


def test_write_trace_appends_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    now = ET.localize(datetime(2026, 5, 30, 9, 16))
    rec = build_trace_record(_setup(), _trace(), now, source="live")
    path = write_trace(rec)
    assert path.endswith(os.path.join("learning", "router_track", "2026-05-30.jsonl"))
    write_trace(rec)                                  # second append
    with open(path) as f:
        lines = f.read().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == rec
