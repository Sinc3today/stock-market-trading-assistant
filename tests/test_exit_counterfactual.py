from signals.exit_counterfactual import exit_quality, aggregate_exit_quality


def test_exit_quality_positive_when_exit_beats_hold():
    assert exit_quality(pnl_exit=-10.0, pnl_hold=-80.0) == 70.0


def test_exit_quality_negative_when_we_cut_a_winner():
    assert exit_quality(pnl_exit=20.0, pnl_hold=100.0) == -80.0


def test_aggregate_groups_by_combo_and_reason():
    rows = [
        {"strategy": "put_debit_spread", "dte_bucket": "0DTE",
         "exit_reason": "scratch", "pnl_exit": -10.0, "pnl_hold": -80.0},
        {"strategy": "put_debit_spread", "dte_bucket": "0DTE",
         "exit_reason": "scratch", "pnl_exit": 5.0, "pnl_hold": 50.0},
    ]
    agg = aggregate_exit_quality(rows)
    key = "put_debit_spread|0DTE|scratch"
    assert agg[key]["n"] == 2
    assert agg[key]["mean_exit_quality"] == (70.0 + -45.0) / 2
    assert agg[key]["mean_pnl_exit"] == (-10.0 + 5.0) / 2
    assert agg[key]["mean_pnl_hold"] == (-80.0 + 50.0) / 2
