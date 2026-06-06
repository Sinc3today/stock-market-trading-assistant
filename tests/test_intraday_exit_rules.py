from datetime import time
from signals.intraday_exit_rules import evaluate_intraday_exit, ExitDecision


def _pos(**kw):
    base = dict(strategy="put_debit_spread", dte_bucket="0DTE",
                max_profit=300.0, max_loss=56.0)
    base.update(kw)
    return base


def _rule(**kw):
    base = dict(profit_target_pct=1.0, stop_pct=0.75,
                scratch_time=None, scratch_theta=0.0, hard_close_time=None)
    base.update(kw)
    return base


def test_profit_target_fires_first_even_after_scratch_time():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": 300.0, "exit_price": 4.0},
        now_et=time(13, 30), rule=_rule(scratch_time="13:00", hard_close_time="14:00"))
    assert d is not None and d.reason == "target" and d.exit_price == 4.0


def test_stop_fires_when_pnl_below_negative_stop():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -50.0, "exit_price": 0.1},
        now_et=time(10, 0), rule=_rule())
    assert d is not None and d.reason == "stop"


def test_scratch_fires_when_not_working_at_scratch_time():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(13, 0), rule=_rule(scratch_time="13:00", scratch_theta=0.0))
    assert d is not None and d.reason == "scratch" and d.fired_at == "13:00"


def test_scratch_does_not_fire_for_a_working_trade():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": 40.0, "exit_price": 1.2},
        now_et=time(13, 5), rule=_rule(scratch_time="13:00", scratch_theta=0.10,
                                       profit_target_pct=1.0, stop_pct=None))
    assert d is None


def test_scratch_does_not_fire_before_scratch_time():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(12, 55), rule=_rule(scratch_time="13:00", stop_pct=None))
    assert d is None


def test_hard_close_fires_unconditionally_at_time():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(14, 0), rule=_rule(hard_close_time="14:00", stop_pct=None))
    assert d is not None and d.reason == "hard_close" and d.fired_at == "14:00"


def test_scratch_precedes_hard_close_when_both_eligible():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(14, 30),
        rule=_rule(scratch_time="13:00", hard_close_time="14:00", stop_pct=None))
    assert d.reason == "scratch"


def test_enable_time_exits_false_skips_time_rules():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": -5.0, "exit_price": 0.5},
        now_et=time(14, 30),
        rule=_rule(scratch_time="13:00", hard_close_time="14:00", stop_pct=None),
        enable_time_exits=False)
    assert d is None


def test_no_rules_set_returns_none():
    d = evaluate_intraday_exit(
        _pos(), mark={"pnl": 10.0, "exit_price": 1.0},
        now_et=time(11, 0), rule=_rule(stop_pct=None))
    assert d is None
