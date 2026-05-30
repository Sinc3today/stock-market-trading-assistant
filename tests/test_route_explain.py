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


from signals.intraday_entry_router import route, route_explain


class _CapBroker:
    """Broker whose combo has 0 OPEN trades but a today-count at the cap —
    isolates the dedup CAP branch (the open-position branch can't fire)."""
    def __init__(self):
        self.trades = self
    def get_trades_by(self, *, strategy, dte_bucket):
        return []
    def _entry_count_today_by_combo(self, strategy, dte_bucket):
        return 2   # == config.INTRADAY_PER_COMBO_DAILY_CAP


def _accepted_buckets(trace):
    return [a["dte_bucket"] for a in trace["accepted"]]


def test_route_explain_tier_fail():
    s = _setup(conviction="standard")               # below ENTRY_TIER_MINIMUM="high"
    now = ET.localize(datetime(2024, 7, 15, 10, 0))
    trace = route_explain(s, now, _MockBroker())
    assert trace["passed_tier"] is False
    assert trace["accepted"] == []
    assert len(trace["rejected"]) == 1
    assert trace["rejected"][0]["gate"] == "tier"


def test_route_explain_morning_high_accepts_0dte_rejects_1_3dte_on_dte():
    s = _setup(conviction="high", score=70)
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # Monday AM
    trace = route_explain(s, now, _MockBroker())
    assert _accepted_buckets(trace) == ["0DTE"]
    assert [r for r in trace["rejected"] if r["gate"] == "dte"]
    assert all(r["dte_bucket"] == "1-3DTE" for r in trace["rejected"] if r["gate"] == "dte")


def test_route_explain_ultra_conviction_accepts_both_no_rejects():
    s = _setup(conviction="high", score=90)           # >= ULTRA_CONVICTION_DOUBLE_DTE_SCORE
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # Monday (not Friday)
    trace = route_explain(s, now, _MockBroker())
    assert sorted(_accepted_buckets(trace)) == ["0DTE", "1-3DTE"]
    assert trace["rejected"] == []


def test_route_explain_dedup_open_position_rejects():
    s = _setup(strategy="iron_condor", conviction="high", score=70)
    broker = _MockBroker()
    broker.record_open(strategy="iron_condor", dte_bucket="0DTE")
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # morning → candidate 0DTE
    trace = route_explain(s, now, broker)
    assert trace["accepted"] == []
    assert any(r["gate"] == "dedup" and r["dte_bucket"] == "0DTE" for r in trace["rejected"])


def test_route_explain_dedup_cap_rejects():
    s = _setup(strategy="iron_condor", conviction="high", score=70)
    now = ET.localize(datetime(2024, 7, 15, 10, 0))   # morning → candidate 0DTE
    trace = route_explain(s, now, _CapBroker())
    assert trace["accepted"] == []
    assert any(r["gate"] == "dedup" and "cap" in r["detail"] for r in trace["rejected"])


def test_route_explain_accept_set_matches_route_across_fixtures():
    cases = [
        (_setup(conviction="standard"),               ET.localize(datetime(2024, 7, 15, 10, 0))),
        (_setup(conviction="high", score=70),         ET.localize(datetime(2024, 7, 15, 10, 0))),
        (_setup(conviction="high", score=70),         ET.localize(datetime(2024, 7, 15, 13, 0))),
        (_setup(conviction="high", score=90),         ET.localize(datetime(2024, 7, 15, 10, 0))),
        (_setup(conviction="high", score=90),         ET.localize(datetime(2024, 7, 12, 13, 0))),  # Fri PM
    ]
    for s, now in cases:
        b1, b2 = _MockBroker(), _MockBroker()
        explain_accepted = [a["dte_bucket"] for a in route_explain(s, now, b1)["accepted"]]
        route_accepted   = [d["dte_bucket"] for d in route(s, now, b2)]
        assert explain_accepted == route_accepted, (s.conviction, s.score, now)
