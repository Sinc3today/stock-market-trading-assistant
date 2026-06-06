import pandas as pd
from datetime import datetime, timedelta


def make_spy_intraday(day, start_price=500.0, n=78):
    """5-min UTC-indexed SPY frame for an RTH session (09:30-16:00 ET)."""
    idx = pd.date_range(f"{day} 13:30:00", periods=n, freq="5min", tz="UTC")
    closes = [start_price + i * 0.05 for i in range(n)]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1000] * n}, index=idx)


def _fake_option_price(contract, spot=500.0):
    """Deterministic, strike-aware option price so multi-leg spreads have a
    positive net premium (a flat per-leg price would value every debit/credit
    spread at exactly 0 and the structure builder would reject the trade).

    OCC ticker: O:SPY240301C00505000 — last 8 digits = strike * 1000,
    the char before them is C/P. Calls decrease in strike, puts increase in
    strike (a crude but monotonic curve around `spot`)."""
    cp = contract[-9]
    strike = int(contract[-8:]) / 1000.0
    if cp == "C":
        return max(0.05, 5.0 - 0.5 * (strike - spot))
    return max(0.05, 5.0 + 0.5 * (strike - spot))


class FakeOptionsHistory:
    """Returns a strike-aware, always-priceable 5-min option bar frame."""
    def get_aggs(self, contract, mult, span, start, end):
        idx = pd.date_range(f"{start} 13:30:00", periods=78, freq="5min", tz="UTC")
        px = _fake_option_price(contract)
        return pd.DataFrame({"close": [px] * 78}, index=idx)
