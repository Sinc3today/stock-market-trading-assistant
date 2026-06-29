"""signals/condor_calc.py -- on-demand iron-condor calculator for the copilot.

Builds a condor at the CURRENT SPY price so the user can mirror it on Robinhood
even if they missed the morning notification. Matches their real structure:
0.20-delta short put + call, $5 protective wings, ~45 DTE; priced (credit / max
profit / max loss / breakevens) with the live-parity BS engine at the current VIX.

Pure — spot + vix in, a condor dict out. No chain/network needed.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from learning.exit_manager import bs_price


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _delta(opt_type: str, spot: float, strike: float, t: float, sigma: float) -> float:
    """Black-Scholes delta (r=0), matching bs_price's assumptions. Calls in
    [0,1], puts in [-1,0]."""
    if t <= 0 or sigma <= 0 or strike <= 0:
        if opt_type == "call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return _norm_cdf(d1) if opt_type == "call" else _norm_cdf(d1) - 1.0


def _strike_for_delta(opt_type: str, spot: float, t: float, sigma: float,
                      target: float) -> float:
    """Nearest $1 OTM strike whose |delta| is closest to `target`. Calls scanned
    above spot, puts below."""
    best_k, best_err = None, 1e9
    if opt_type == "call":
        rng = range(int(round(spot)) + 1, int(round(spot * 1.20)))
    else:
        rng = range(int(round(spot * 0.80)), int(round(spot)))
    for k in rng:
        err = abs(abs(_delta(opt_type, spot, float(k), t, sigma)) - target)
        if err < best_err:
            best_err, best_k = err, float(k)
    return best_k if best_k is not None else round(spot)


def _nearest_friday(d: date) -> date:
    return d + timedelta(days=(4 - d.weekday()) % 7)


def build_condor(spot: float, vix: float | None = None, dte: int = 45,
                 short_delta: float = 0.20, wing: float = 5.0,
                 today: date | None = None) -> dict:
    """Build + price a condor at `spot`. Returns strikes, legs (RH-shaped),
    credit, max profit/loss, breakevens, and the target expiry."""
    sigma = (vix if vix and vix > 0 else 18.0) / 100.0
    t = max(dte, 1) / 365.0
    short_call = _strike_for_delta("call", spot, t, sigma, short_delta)
    short_put = _strike_for_delta("put", spot, t, sigma, short_delta)
    long_call = short_call + wing
    long_put = short_put - wing

    def px(opt, k):
        return bs_price(opt, spot, k, t, sigma)

    credit = round(px("call", short_call) + px("put", short_put)
                   - px("call", long_call) - px("put", long_put), 2)
    credit = max(0.0, credit)
    expiry = _nearest_friday((today or date.today()) + timedelta(days=dte))
    exp_iso = expiry.isoformat()

    legs = [
        {"action": "BUY",  "option_type": "CALL", "strike": long_call,  "expiry": exp_iso},
        {"action": "SELL", "option_type": "CALL", "strike": short_call, "expiry": exp_iso},
        {"action": "BUY",  "option_type": "PUT",  "strike": long_put,   "expiry": exp_iso},
        {"action": "SELL", "option_type": "PUT",  "strike": short_put,  "expiry": exp_iso},
    ]
    return {
        "spot": round(spot, 2),
        "short_call": short_call, "long_call": long_call,
        "short_put": short_put, "long_put": long_put,
        "wing": wing,
        "credit": credit,
        "max_profit": round(credit * 100, 2),
        "max_loss": round((wing - credit) * 100, 2),
        "breakeven_low": round(short_put - credit, 2),
        "breakeven_high": round(short_call + credit, 2),
        "expiry": exp_iso,
        "legs": legs,
    }
