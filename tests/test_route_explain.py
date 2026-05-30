import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from signals.spy_options_engine import SPYSetup
from signals.intraday_entry_router import _dedup_partition, _dedup_filter
from backtests.intraday_router_wf import _MockBroker

ET = pytz.timezone("US/Eastern")


def _setup(strategy="iron_condor", conviction="high", score=70, direction="neutral", trend="range-bound"):
    return SPYSetup(strategy=strategy, conviction=conviction, timeframe="intraday",
                    score=score, reasons=["r1", "r2"], direction=direction, trend=trend)


def test_dedup_partition_all_clear_returns_buckets_and_no_rejects():
    broker = _MockBroker()
    allowed, rejected = _dedup_partition("iron_condor", ["0DTE", "1-3DTE"], broker)
    assert allowed == ["0DTE", "1-3DTE"]
    assert rejected == []


def test_dedup_partition_open_position_rejects_with_reason():
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    allowed, rejected = _dedup_partition("iron_condor", ["0DTE", "1-3DTE"], broker)
    assert allowed == ["1-3DTE"]
    assert len(rejected) == 1
    bucket, reason = rejected[0]
    assert bucket == "0DTE"
    assert "open position" in reason and "iron_condor" in reason


def test_dedup_filter_still_returns_same_list_as_partition_allowed():
    # Regression: route() relies on _dedup_filter; it must equal partition's allowed.
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    allowed, _ = _dedup_partition("iron_condor", ["0DTE", "1-3DTE"], broker)
    assert _dedup_filter("iron_condor", ["0DTE", "1-3DTE"], broker) == allowed


from signals.intraday_entry_router import _dte_reject_detail


def test_dte_reject_detail_friday_pm_drops_1_3dte():
    fri_pm = ET.localize(datetime(2024, 7, 12, 13, 0))   # 2024-07-12 is a Friday
    detail = _dte_reject_detail(_setup(), fri_pm, "1-3DTE")
    assert "Friday-PM safeguard" in detail


def test_dte_reject_detail_morning_drops_1_3dte():
    mon_am = ET.localize(datetime(2024, 7, 15, 10, 0))   # Monday 10:00 ET (pre-cutoff)
    detail = _dte_reject_detail(_setup(), mon_am, "1-3DTE")
    assert "morning" in detail


def test_dte_reject_detail_afternoon_drops_0dte():
    mon_pm = ET.localize(datetime(2024, 7, 15, 13, 0))   # Monday 13:00 ET (post-cutoff)
    detail = _dte_reject_detail(_setup(), mon_pm, "0DTE")
    assert "afternoon" in detail
