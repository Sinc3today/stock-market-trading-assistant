from backtests.intraday_router_wf import arm_verdicts, parity_divergence


def _mk(strategy, bucket, pnl_real, pnl_bs):
    path = [{"t": "13:00", "pnl": pnl_real, "exit_price": 0.4,
             "pnl_bs": pnl_bs, "exit_price_bs": 0.4},
            {"t": "15:55", "pnl": -40.0, "exit_price": 0.05,
             "pnl_bs": -40.0, "exit_price_bs": 0.05}]
    return {"strategy": strategy, "dte_bucket": bucket, "max_profit": 300.0,
            "max_loss": 56.0, "profit_target_pct": 1.0, "stop_pct": None,
            "path": path, "pnl_hold": -40.0}


def test_arm_verdicts_reports_per_combo_per_arm_mean():
    trades = [_mk("put_debit_spread", "0DTE", -5.0, -5.0) for _ in range(12)]
    av = arm_verdicts(trades)
    combo = av["put_debit_spread|0DTE"]
    assert combo["hard_close@13:00"]["n"] == 12
    assert combo["hard_close@13:00"]["mean_pnl"] == -5.0
    assert combo["baseline"]["mean_pnl"] == -40.0


def test_parity_divergence_flags_when_bs_mark_disagrees():
    trades = [_mk("put_debit_spread", "0DTE", -5.0, 50.0) for _ in range(10)]
    pd_report = parity_divergence(trades)
    assert "put_debit_spread|0DTE" in pd_report
    row = pd_report["put_debit_spread|0DTE"]["hard_close@13:00"]
    assert 0.0 <= row["agree_frac"] <= 1.0
    assert "passes" in row
