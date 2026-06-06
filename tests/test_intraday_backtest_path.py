from datetime import date
from backtests.intraday_backtest import simulate_0dte_day
from tests.helpers_intraday import make_spy_intraday, FakeOptionsHistory


def test_simulate_0dte_day_returns_path_with_both_marks():
    day = date(2024, 3, 1)
    spy = make_spy_intraday(day)
    oh  = FakeOptionsHistory()
    result = simulate_0dte_day(day, "bull_debit", spy, oh,
                               require_confirmation=False)
    assert result is not None
    assert "path" in result
    assert len(result["path"]) >= 1
    row = result["path"][0]
    for k in ("t", "pnl", "exit_price", "pnl_bs", "exit_price_bs"):
        assert k in row
    assert "pnl_hold" in result
