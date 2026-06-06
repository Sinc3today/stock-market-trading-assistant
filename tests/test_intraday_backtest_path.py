from datetime import date
from backtests.intraday_backtest import (
    simulate_0dte_day, build_0dte_legs, is_credit_structure,
)
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


def test_simulate_0dte_day_returns_max_profit_and_max_loss_for_debit():
    """The sim must surface its OWN max_profit/max_loss so the arm-replay layer
    never reconstructs them (drift risk). For a debit spread:
        max_loss   = entry_px * 100
        max_profit = (width - entry_px) * 100
    computed off the SAME post-slippage entry_px the sim used internally."""
    day = date(2024, 3, 1)
    spy = make_spy_intraday(day)
    oh  = FakeOptionsHistory()
    structure = "bull_debit"
    result = simulate_0dte_day(day, structure, spy, oh,
                               require_confirmation=False)
    assert result is not None
    assert "max_profit" in result
    assert "max_loss" in result

    # Independently reproduce the sim's max_profit/max_loss from its OWN reported
    # entry_px (the post-slippage value the result dict carries) and the leg
    # width the sim builds. This pins both values to the sim's exact convention.
    assert not is_credit_structure(structure)  # this case is a debit
    entry_px = result["entry_px"]
    legs = build_0dte_legs(result["entry_spot"], structure)
    width = abs(legs[0]["strike"] - legs[1]["strike"])
    assert result["max_loss"] == round(entry_px * 100, 2)
    assert result["max_profit"] == round((width - entry_px) * 100, 2)
    assert result["max_profit"] > 0
