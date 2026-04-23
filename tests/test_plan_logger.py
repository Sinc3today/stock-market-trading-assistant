"""
tests/test_plan_logger.py — Test PlanLogger
All tests use tmp_path so no real files are written.
"""
import pytest, sys, os, json
from datetime import date, timedelta
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from journal.plan_logger import PlanLogger

@pytest.fixture
def logger_inst(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return PlanLogger()

def _make_plan(d=None, regime="trending_up_calm", tradeable=True):
    return {"date": (d or date.today()).isoformat(), "ticker":"SPY",
            "regime": regime, "play": "BULL CALL DEBIT SPREAD",
            "confidence":0.85, "strategy":"debit_spread",
            "legs":[], "max_profit":"~$300", "max_loss":"~$200",
            "executed":False, "trade_id":None,
            "action": None if tradeable else "SKIP"}

def test_save_plan_returns_true(logger_inst):
    assert logger_inst.save_plan(_make_plan()) is True
    print("\n✅ save_plan returns True")

def test_save_plan_persists(logger_inst):
    logger_inst.save_plan(_make_plan())
    p = logger_inst.get_today()
    assert p is not None
    assert p["regime"] == "trending_up_calm"
    print(f"\n✅ Plan persisted: {p['regime']}")

def test_save_plan_is_idempotent(logger_inst):
    logger_inst.save_plan(_make_plan())
    logger_inst.save_plan(_make_plan())   # second save same date
    recent = logger_inst.get_recent(days=7)
    assert len(recent) == 1              # should not duplicate
    print("\n✅ Idempotent save (no duplicates)")

def test_mark_executed(logger_inst):
    today = date.today().isoformat()
    logger_inst.save_plan(_make_plan())
    result = logger_inst.mark_executed(today, "ABC12345")
    assert result is True
    p = logger_inst.get_plan(today)
    assert p["executed"]  is True
    assert p["trade_id"]  == "ABC12345"
    print(f"\n✅ Marked executed → trade_id: {p['trade_id']}")

def test_mark_executed_missing_date_returns_false(logger_inst):
    assert logger_inst.mark_executed("2000-01-01", "X") is False
    print("\n✅ Missing date returns False gracefully")

def test_get_recent_returns_sorted_newest_first(logger_inst):
    for i in range(5):
        d = date.today() - timedelta(days=i)
        logger_inst.save_plan(_make_plan(d=d))
    recent = logger_inst.get_recent(days=10)
    assert len(recent) == 5
    assert recent[0]["date"] >= recent[-1]["date"]
    print(f"\n✅ get_recent: {len(recent)} plans, newest first")

def test_get_stats(tmp_path, monkeypatch):
    """Use its own fixture so plan count is isolated."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/stats_test/")
    pl = PlanLogger()
    pl.save_plan(_make_plan(regime="trending_up_calm"))
    pl.save_plan(_make_plan(d=date.today()-timedelta(days=1), regime="choppy_low_vol"))
    stats = pl.get_stats()
    assert stats["total"] == 2
    assert "trending_up_calm" in stats["regime_counts"]
    print(f"\n✅ Stats: {stats}")

def test_missing_date_key_returns_false(logger_inst):
    assert logger_inst.save_plan({"regime":"trending_up_calm"}) is False
    print("\n✅ Missing date key handled gracefully")
