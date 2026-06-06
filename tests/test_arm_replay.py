from backtests.intraday_router_wf import replay_arms, ARMS


def _trade():
    path = [{"t": "09:45", "pnl": -3.0, "exit_price": 0.5, "pnl_bs": -3.0, "exit_price_bs": 0.5},
            {"t": "13:00", "pnl": -5.0, "exit_price": 0.4, "pnl_bs": -5.0, "exit_price_bs": 0.4},
            {"t": "14:00", "pnl": -8.0, "exit_price": 0.3, "pnl_bs": -8.0, "exit_price_bs": 0.3},
            {"t": "15:55", "pnl": -40.0, "exit_price": 0.05, "pnl_bs": -40.0, "exit_price_bs": 0.05}]
    return {"strategy": "put_debit_spread", "dte_bucket": "0DTE",
            "max_profit": 300.0, "max_loss": 56.0, "profit_target_pct": 1.0,
            "stop_pct": None, "path": path, "pnl_hold": -40.0}


def test_baseline_arm_holds_to_eod():
    res = replay_arms(_trade())
    base = res["baseline"]
    assert base["pnl_exit"] == -40.0
    assert base["exit_reason"] == "eod"
    assert base["exit_quality"] == 0.0


def test_hard_close_1300_exits_early_and_saves_money():
    res = replay_arms(_trade())
    arm = res["hard_close@13:00"]
    assert arm["pnl_exit"] == -5.0
    assert arm["exit_reason"] == "hard_close"
    assert arm["exit_quality"] == 35.0


def test_every_arm_present():
    res = replay_arms(_trade())
    assert set(res.keys()) == set(ARMS.keys())
