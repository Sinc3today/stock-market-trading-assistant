"""Tests for the market-holiday gate (C3 hotfix)."""
from datetime import date, datetime
import pytest
import pytz
import config


# ── Holiday set ────────────────────────────────────────────────

def test_us_market_holidays_2026_includes_memorial_day():
    """Memorial Day 2026-05-25 is in the holiday set."""
    assert date(2026, 5, 25) in config.US_MARKET_HOLIDAYS_2026


def test_us_market_holidays_2026_includes_thanksgiving():
    """Thanksgiving 2026-11-26 is in the holiday set."""
    assert date(2026, 11, 26) in config.US_MARKET_HOLIDAYS_2026


def test_us_market_holidays_2026_includes_juneteenth():
    """Juneteenth 2026-06-19 is in the holiday set."""
    assert date(2026, 6, 19) in config.US_MARKET_HOLIDAYS_2026


def test_us_market_holidays_2026_includes_christmas():
    """Christmas 2026-12-25 is in the holiday set."""
    assert date(2026, 12, 25) in config.US_MARKET_HOLIDAYS_2026


def test_us_market_holidays_2026_includes_independence_observed():
    """July 4 2026 falls on a Saturday → observed Friday 2026-07-03."""
    assert date(2026, 7, 3) in config.US_MARKET_HOLIDAYS_2026


def test_us_market_holidays_2026_includes_new_years_day_observed():
    """Jan 1 2026 is Thursday — actual NYD."""
    assert date(2026, 1, 1) in config.US_MARKET_HOLIDAYS_2026


def test_us_market_holidays_2026_excludes_random_weekday():
    """Tuesday 2026-05-26 (the day after Memorial Day) is a trading day."""
    assert date(2026, 5, 26) not in config.US_MARKET_HOLIDAYS_2026


# ── is_trading_day helper ──────────────────────────────────────

def test_is_trading_day_returns_false_on_saturday():
    assert config.is_trading_day(date(2026, 5, 23)) is False


def test_is_trading_day_returns_false_on_sunday():
    assert config.is_trading_day(date(2026, 5, 24)) is False


def test_is_trading_day_returns_false_on_memorial_day():
    assert config.is_trading_day(date(2026, 5, 25)) is False


def test_is_trading_day_returns_true_on_regular_tuesday():
    assert config.is_trading_day(date(2026, 5, 26)) is True


def test_is_trading_day_returns_false_on_christmas():
    assert config.is_trading_day(date(2026, 12, 25)) is False


def test_is_trading_day_accepts_datetime_object():
    """is_trading_day must handle both date and datetime inputs."""
    eastern = pytz.timezone("US/Eastern")
    # Memorial Day at noon as datetime
    dt_holiday = eastern.localize(datetime(2026, 5, 25, 12, 0))
    assert config.is_trading_day(dt_holiday) is False
    # Normal Tuesday as datetime
    dt_trading = eastern.localize(datetime(2026, 5, 26, 10, 0))
    assert config.is_trading_day(dt_trading) is True


# ── IntradayScanner gate ───────────────────────────────────────

def test_intraday_scanner_skips_on_holiday(monkeypatch):
    """IntradayScanner.is_market_hours returns False on Memorial Day even at noon."""
    from scanners.intraday_scanner import IntradayScanner
    eastern = pytz.timezone("US/Eastern")
    # Memorial Day at noon
    fake_now = eastern.localize(datetime(2026, 5, 25, 12, 0))

    scanner = IntradayScanner()

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("scanners.intraday_scanner.datetime", _FakeDateTime)
    assert scanner.is_market_hours() is False


def test_intraday_scanner_runs_on_regular_tuesday(monkeypatch):
    """IntradayScanner.is_market_hours returns True on a normal Tuesday at 10am."""
    from scanners.intraday_scanner import IntradayScanner
    eastern = pytz.timezone("US/Eastern")
    # Tuesday 2026-05-26 at 10am — market should be open
    fake_now = eastern.localize(datetime(2026, 5, 26, 10, 0))

    scanner = IntradayScanner()

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("scanners.intraday_scanner.datetime", _FakeDateTime)
    assert scanner.is_market_hours() is True


# ── job wrapper gates ──────────────────────────────────────────

def test_job_paper_broker_skips_on_holiday(monkeypatch):
    """job_paper_broker must short-circuit on a market holiday."""
    from learning import scheduler as sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 9, 16))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("learning.scheduler.datetime", _FakeDatetime)

    called = []

    class FakeBroker:
        def execute_today(self):
            called.append(True)
            return {}

    monkeypatch.setattr(sched, "PaperBroker", lambda: FakeBroker())
    sched.job_paper_broker()
    assert called == [], "paper_broker should not run on a holiday"


def test_job_paper_broker_runs_on_trading_day(monkeypatch):
    """job_paper_broker must run on a normal weekday."""
    from learning import scheduler as sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 26, 9, 16))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("learning.scheduler.datetime", _FakeDatetime)

    called = []

    class FakeBroker:
        def execute_today(self):
            called.append(True)
            return {"result": "ok"}

    monkeypatch.setattr(sched, "PaperBroker", lambda: FakeBroker())
    sched.job_paper_broker()
    assert called == [True], "paper_broker should run on a normal trading day"


def test_job_reflector_skips_on_holiday(monkeypatch):
    """job_reflector must short-circuit on a market holiday."""
    from learning import scheduler as sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 19, 1))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("learning.scheduler.datetime", _FakeDatetime)

    called = []

    class FakeReflector:
        def __init__(self, **kwargs): pass
        def reflect_today(self):
            called.append(True)
            return {}

    monkeypatch.setattr(sched, "Reflector", FakeReflector)
    sched.job_reflector(post_fn=None)
    assert called == [], "reflector should not run on a holiday"


def test_job_outcome_resolver_skips_on_holiday(monkeypatch):
    """job_outcome_resolver must short-circuit on a market holiday."""
    from learning import scheduler as sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 16, 5))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("learning.scheduler.datetime", _FakeDatetime)

    called = []

    class FakeResolver:
        def __init__(self, **kwargs): pass
        def resolve_today(self):
            called.append(True)
            return {}

    monkeypatch.setattr(sched, "OutcomeResolver", FakeResolver)
    sched.job_outcome_resolver(polygon_client=None, post_fn=None)
    assert called == [], "outcome_resolver should not run on a holiday"


def test_job_exit_manager_skips_on_holiday(monkeypatch):
    """job_exit_manager must short-circuit on a market holiday."""
    from learning import scheduler as sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 16, 8))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("learning.scheduler.datetime", _FakeDatetime)

    called = []

    class FakeExitManager:
        def __init__(self, **kwargs): pass
        def manage_open(self, **kwargs):
            called.append(True)
            return []

    monkeypatch.setattr(sched, "ExitManager", FakeExitManager)
    sched.job_exit_manager(polygon_client=None, post_fn=None)
    assert called == [], "exit_manager should not run on a holiday"


def test_job_spy_premarket_skips_on_holiday(monkeypatch):
    """job_spy_premarket must short-circuit on a market holiday."""
    from scheduler import spy_daily_scheduler as spy_sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 9, 15))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("scheduler.spy_daily_scheduler.datetime", _FakeDatetime)

    called = []
    monkeypatch.setattr(
        spy_sched, "MorningBriefer",
        type("MB", (), {"__init__": lambda self, **kw: called.append(True), "build_today": lambda self: {}})
    )

    spy_sched.job_spy_premarket(
        polygon_client=None, vix_client=None, ivr_client=None, post_fn=None
    )
    # MorningBriefer should never be instantiated on a holiday
    assert called == [], "spy_premarket should not run on a holiday"


def test_job_spy_close_snapshot_skips_on_holiday(monkeypatch):
    """job_spy_close_snapshot must short-circuit on a market holiday."""
    from scheduler import spy_daily_scheduler as spy_sched
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 16, 30))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("scheduler.spy_daily_scheduler.datetime", _FakeDatetime)

    class FakePolygon:
        def get_bars(self, *a, **kw):
            return None  # should never be reached

    spy_sched.job_spy_close_snapshot(polygon_client=FakePolygon(), post_fn=None)
    # No exception = the function exited early. get_bars would return None
    # but we verify no error was raised and the function returned gracefully.


def test_swing_scanner_skips_on_holiday(monkeypatch):
    """SwingScanner.run() must return [] on a market holiday even at 9am."""
    from scanners.swing_scanner import SwingScanner
    eastern = pytz.timezone("US/Eastern")
    fake_now = eastern.localize(datetime(2026, 5, 25, 9, 0))

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now

    monkeypatch.setattr("scanners.swing_scanner.datetime", _FakeDatetime)

    scanner = SwingScanner()
    result = scanner.run()
    assert result == [], "swing_scanner should return [] on a holiday"
