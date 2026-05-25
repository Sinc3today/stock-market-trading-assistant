"""Phase 3: intraday_entry_router applies H2 DTE assignment + D dedup
to convert a SPYSetup into 0..2 setup_dicts ready for execute_signal."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, date, time, timedelta
import pytz
import pytest

from signals.intraday_entry_router import route, _build_setup_dict, _synthesize_legs
from signals.spy_options_engine import SPYSetup
from learning.paper_broker import PaperBroker
from learning.exit_manager import ExitManager
from journal.trade_recorder import TradeRecorder

EASTERN = pytz.timezone("US/Eastern")


def _setup(strategy="call_debit_spread", conviction="high", score=75,
           direction="bullish"):
    return SPYSetup(
        strategy=strategy, conviction=conviction, timeframe="intraday",
        score=score, reasons=["test"], direction=direction, spy_price=500.0,
    )


def _now(hour=10, minute=0, weekday=2):
    """Build an ET datetime for a given hour/minute on a given weekday.
    Weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri."""
    # 2026-05-25 is Monday → +weekday gives the right day-of-week
    d = date(2026, 5, 25 + weekday)
    return EASTERN.localize(datetime(d.year, d.month, d.day, hour, minute))


# ── H2 DTE assignment ────────────────────────────────────────────────────

def test_morning_high_conv_assigns_0dte(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=75)        # high-conv but not ultra
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "0DTE"
    assert result[0]["strategy"] == "call_debit_spread"
    assert result[0]["book"] == "disciplined"


def test_afternoon_high_conv_assigns_1_3dte(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=75)
    result = route(setup, _now(hour=14), broker)
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "1-3DTE"


def test_friday_pm_defaults_to_0dte(tmp_path, monkeypatch):
    """Friday safeguard: PM signal opens 0DTE (no weekend exposure)
    despite the time-of-day rule saying afternoon → 1-3DTE."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=75)
    result = route(setup, _now(hour=14, weekday=4), broker)   # Friday 14:00
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "0DTE"


def test_ultra_conv_morning_opens_both_dtes(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=92)        # ultra-conv (≥ 85)
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 2
    buckets = {r["dte_bucket"] for r in result}
    assert buckets == {"0DTE", "1-3DTE"}


def test_ultra_conv_on_friday_pm_only_0dte(tmp_path, monkeypatch):
    """Friday safeguard wins over ultra-conv doubling."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=92)
    result = route(setup, _now(hour=14, weekday=4), broker)   # Friday PM
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "0DTE"


# ── Entry-tier filter ────────────────────────────────────────────────────

def test_standard_conviction_returns_empty(tmp_path, monkeypatch):
    """Phase 3 ships with ENTRY_TIER_MINIMUM='high'. Standard-tier setups
    don't open positions."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=55, conviction="standard")
    assert route(setup, _now(hour=10), broker) == []


def test_watch_tier_returns_empty(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(score=30, conviction="watch")
    assert route(setup, _now(hour=10), broker) == []


# ── D dedup rule ─────────────────────────────────────────────────────────

def test_dedup_blocks_when_combo_already_open(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1, trade_type="option_spread",
        strategy="call_debit_spread", direction="bullish", mode="intraday",
        legs=[], max_profit=200.0, max_loss=100.0,
        notes="[AUTO-PAPER] open", dte_bucket="0DTE", book="disciplined",
    )
    setup = _setup(strategy="call_debit_spread", score=78)
    result = route(setup, _now(hour=10), broker)
    assert result == []


def test_dedup_blocks_when_per_day_cap_reached(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    for _ in range(2):
        tid = rec.log_entry(
            ticker="SPY", entry_price=1.0, size=1, trade_type="option_spread",
            strategy="call_debit_spread", direction="bullish", mode="intraday",
            legs=[], max_profit=200.0, max_loss=100.0,
            notes="[AUTO-PAPER] test", dte_bucket="0DTE", book="disciplined",
        )
        rec.log_exit(tid, exit_price=0.50, exit_reason="stop")
    setup = _setup(strategy="call_debit_spread", score=78)
    result = route(setup, _now(hour=10), broker)
    assert result == []


def test_ultra_conv_with_one_combo_blocked_returns_only_other(tmp_path, monkeypatch):
    """Ultra-conv would normally return both. If 0DTE is dedup-blocked but
    1-3DTE isn't, return only the 1-3DTE."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    rec = TradeRecorder()
    rec.log_entry(
        ticker="SPY", entry_price=1.0, size=1, trade_type="option_spread",
        strategy="iron_condor", direction="neutral", mode="intraday",
        legs=[], max_profit=200.0, max_loss=100.0,
        notes="[AUTO-PAPER] open", dte_bucket="0DTE", book="disciplined",
    )
    setup = _setup(strategy="iron_condor", conviction="high", score=92,
                    direction="neutral")
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 1
    assert result[0]["dte_bucket"] == "1-3DTE"


# ── Setup_dict shape (placeholder pricing for Phase 3) ───────────────────

def test_setup_dict_has_expected_shape(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(strategy="iron_condor", score=78, direction="neutral")
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 1
    sd = result[0]
    assert "date" in sd
    assert sd["strategy"]   == "iron_condor"
    assert sd["dte_bucket"] == "0DTE"
    assert sd["book"]       == "disciplined"
    assert sd["direction"]  == "neutral"
    # Phase 3 placeholders — replaced by Phase 4 structure builder.
    assert sd["entry_price"] == 1.0
    assert sd["max_profit"]  == 200.0
    assert sd["max_loss"]    == 100.0
    # C4 hotfix: legs now has synthetic placeholder(s) so ExitManager can dispatch.
    assert len(sd["legs"]) >= 1


# ── C4 hotfix: synthetic leg shape + ExitManager dispatch ────────────────

def test_synthesize_legs_0dte_expiration_is_today():
    """0DTE synthetic leg must have expiration == today so ExitManager
    can compute DTE=0 and dispatch the position on the same day."""
    today = date(2026, 5, 27)  # Tuesday after Memorial Day
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 10, 0))
    legs = _synthesize_legs("call_debit_spread", "0DTE", now)
    assert len(legs) >= 1
    leg = legs[0]
    assert leg["expiry"] == today.isoformat()
    assert leg["expiration"] == today.isoformat()
    assert leg["synthetic"] is True


def test_synthesize_legs_1_3dte_expiration_is_today_plus_2():
    """1-3DTE synthetic leg uses today+2 (midpoint of range)."""
    today = date(2026, 5, 27)
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 14, 0))
    legs = _synthesize_legs("iron_condor", "1-3DTE", now)
    assert len(legs) >= 1
    leg = legs[0]
    expected = (today + timedelta(days=2)).isoformat()
    assert leg["expiry"] == expected
    assert leg["expiration"] == expected
    assert leg["synthetic"] is True


def test_synthesize_legs_45dte_expiration_is_today_plus_45():
    """45DTE synthetic leg uses today+45 calendar days."""
    today = date(2026, 5, 27)
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 10, 0))
    legs = _synthesize_legs("iron_condor", "45DTE", now)
    assert len(legs) >= 1
    leg = legs[0]
    expected = (today + timedelta(days=45)).isoformat()
    assert leg["expiry"] == expected
    assert leg["expiration"] == expected
    assert leg["synthetic"] is True


def test_synthesize_legs_unknown_bucket_defaults_to_today():
    """Unknown dte_bucket falls back to today (safe default)."""
    today = date(2026, 5, 27)
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 10, 0))
    legs = _synthesize_legs("iron_condor", "UNKNOWN_BUCKET", now)
    assert len(legs) >= 1
    leg = legs[0]
    assert leg["expiry"] == today.isoformat()
    assert leg["expiration"] == today.isoformat()


def test_synthetic_leg_has_both_expiry_keys():
    """Both 'expiry' (TradeRecorder convention) and 'expiration'
    (exit_manager convention) must be present on every synthetic leg."""
    today = date(2026, 5, 27)
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 10, 0))
    for bucket in ("0DTE", "1-3DTE", "45DTE"):
        legs = _synthesize_legs("iron_condor", bucket, now)
        for leg in legs:
            assert "expiry" in leg, f"'expiry' missing for {bucket}"
            assert "expiration" in leg, f"'expiration' missing for {bucket}"
            assert leg["expiry"] == leg["expiration"], (
                f"expiry/expiration mismatch for {bucket}: "
                f"{leg['expiry']} vs {leg['expiration']}"
            )


def test_synthetic_leg_has_synthetic_marker():
    """synthetic=True flag must be present so Phase 4b structure builder
    can identify placeholders vs real legs."""
    today = date(2026, 5, 27)
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 10, 0))
    legs = _synthesize_legs("call_debit_spread", "0DTE", now)
    assert all(leg.get("synthetic") is True for leg in legs)


def test_route_output_legs_non_empty(tmp_path, monkeypatch):
    """Full route() must produce setup_dicts with non-empty legs list
    (C4 hotfix — was returning legs=[] which orphaned Phase 3 positions)."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    broker = PaperBroker()
    setup = _setup(strategy="iron_condor", score=78, direction="neutral")
    result = route(setup, _now(hour=10), broker)
    assert len(result) == 1
    assert len(result[0]["legs"]) >= 1


def test_exit_manager_nearest_expiration_with_synthetic_leg():
    """Critical regression test: ExitManager._nearest_expiration must return
    a real date (not None) when given a synthetic leg produced by the router.

    The empty-legs bug caused _nearest_expiration to return None -> _evaluate
    short-circuited -> Phase 3 positions never closed -> slot accumulation."""
    today = date(2026, 5, 27)
    now = EASTERN.localize(datetime(today.year, today.month, today.day, 10, 0))

    for bucket, expected_days in [("0DTE", 0), ("1-3DTE", 2), ("45DTE", 45)]:
        legs = _synthesize_legs("iron_condor", bucket, now)
        result = ExitManager._nearest_expiration(legs)
        assert result is not None, (
            f"_nearest_expiration returned None for {bucket} synthetic legs — "
            f"exit pipeline would never dispatch this position"
        )
        expected_date = today + timedelta(days=expected_days)
        assert result == expected_date, (
            f"_nearest_expiration returned {result}, expected {expected_date} "
            f"for dte_bucket={bucket}"
        )
