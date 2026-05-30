import json
import os
import sys
from datetime import date

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from signals.spy_options_engine import SPYSetup

ET = pytz.timezone("US/Eastern")


def _setup(strategy="iron_condor", conviction="high", score=70):
    return SPYSetup(strategy=strategy, conviction=conviction, timeframe="intraday",
                    score=score, reasons=["r"], direction="neutral", trend="range-bound")


def _read_all(track_dir):
    rows = []
    for fn in sorted(os.listdir(track_dir)):
        with open(os.path.join(track_dir, fn)) as f:
            rows += [json.loads(ln) for ln in f if ln.strip()]
    return rows


def test_backfill_two_weekdays_writes_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    # Mon 2024-07-15 and Tue 2024-07-16, one setup each.
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup()])
    n = bf.backfill(date(2024, 7, 15), date(2024, 7, 16))
    assert n == 2
    rows = _read_all(os.path.join(str(tmp_path), "learning", "router_track"))
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"backfill"}


def test_backfill_skips_weekend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup()])
    # 2024-07-13 Sat, 2024-07-14 Sun → zero rows.
    n = bf.backfill(date(2024, 7, 13), date(2024, 7, 14))
    assert n == 0


def test_backfill_fresh_broker_per_day_no_cross_day_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    # Same single iron_condor setup each day; morning eval → 0DTE accepted.
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup()])
    bf.backfill(date(2024, 7, 15), date(2024, 7, 16))
    rows = _read_all(os.path.join(str(tmp_path), "learning", "router_track"))
    # Day 2 must still ACCEPT (fresh broker) — not be dedup-blocked by day 1.
    assert all(r["accepted"] for r in rows)


def test_backfill_accepted_recorded_forward_same_day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    # Two identical iron_condor setups SAME day → 2nd hits dedup (open in combo).
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [_setup(), _setup()])
    bf.backfill(date(2024, 7, 15), date(2024, 7, 15))
    rows = _read_all(os.path.join(str(tmp_path), "learning", "router_track"))
    assert len(rows) == 2
    assert rows[0]["accepted"] == ["0DTE"]
    assert rows[1]["accepted"] == []          # blocked by the first's open position
    assert any(x["gate"] == "dedup" for x in rows[1]["rejected"])


def test_backfill_empty_setups_day_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    import backtests.router_track_backfill as bf
    monkeypatch.setattr(bf, "build_historical_setup", lambda d: [])
    n = bf.backfill(date(2024, 7, 15), date(2024, 7, 15))
    assert n == 0
