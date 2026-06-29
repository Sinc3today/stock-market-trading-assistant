"""backtests/structure_comparison.py -- capital efficiency: condor vs narrow condor vs butterfly.

The user's edge is the iron condor (a CREDIT, so RH ties up the max loss as
collateral). Question: can we get a similar range-bound bet for less capital by
narrowing the wings or switching to a long (debit) butterfly?

Apples-to-apples: the SAME regime-gated entry days the bot actually trades
(CHOPPY_LOW_VOL / CHOPPY_TRANSITION), the SAME BS pricing (live-parity bs_price),
the SAME frictions (slippage + commission) and the SAME 70%-profit-target /
21-DTE management for all three. Only the STRUCTURE differs. Reports win-rate,
P&L, and capital-per-trade (= the collateral RH holds).

Structures (shorts 2.5% OTM, matching the proven condor):
  condor_2pct  : sell 2.5% OTM put+call, BUY wings 2.0% beyond  (as-is)
  condor_1pct  : same shorts, wings 1.0% beyond                 (narrower)
  butterfly    : long call fly [0.975S, S, 1.025S]              (debit; same win zone)

Model-priced (BS off SPY + VIX), so treat absolutes as approximate — the RELATIVE
comparison across structures is the signal.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from learning.exit_manager import bs_price, PROFIT_TARGET_PCT, DTE_CLOSE_THRESHOLD, EXIT_SLIPPAGE
from signals.regime_detector import RegimeDetector, Regime

ENTRY_DTE = 45
SHORT_OFFSET_PCT = 0.025
COMMISSION_PER_LEG = 0.65
ENTRY_SLIPPAGE = EXIT_SLIPPAGE
CONDOR_REGIMES = {Regime.CHOPPY_LOW_VOL, Regime.CHOPPY_TRANSITION}


def condor_legs(spot, wing_pct):
    k = lambda m: round(spot * m, 2)
    return [
        {"action": "SELL", "type": "put",  "strike": k(1 - SHORT_OFFSET_PCT)},
        {"action": "BUY",  "type": "put",  "strike": k(1 - SHORT_OFFSET_PCT - wing_pct)},
        {"action": "SELL", "type": "call", "strike": k(1 + SHORT_OFFSET_PCT)},
        {"action": "BUY",  "type": "call", "strike": k(1 + SHORT_OFFSET_PCT + wing_pct)},
    ]


def butterfly_legs(spot):
    # long call butterfly spanning the condor's shorts: profits in the SAME
    # +-2.5% zone, peaking at the center (current price). A debit.
    kp = round(spot * (1 - SHORT_OFFSET_PCT), 2)
    kc = round(spot * (1 + SHORT_OFFSET_PCT), 2)
    km = round(spot, 2)
    return [
        {"action": "BUY",  "type": "call", "strike": kp},
        {"action": "SELL", "type": "call", "strike": km},
        {"action": "SELL", "type": "call", "strike": km},
        {"action": "BUY",  "type": "call", "strike": kc},
    ]


def _net_debit_bs(legs, spot, sigma, t):
    """Net price of the position as if BUYING it (>0 debit, <0 credit), per share."""
    nd = 0.0
    for leg in legs:
        p = bs_price(leg["type"], spot, leg["strike"], t, sigma)
        nd += p if leg["action"] == "BUY" else -p
    return nd


def _value_at_expiry(legs, S):
    v = 0.0
    for leg in legs:
        intr = max(0.0, S - leg["strike"]) if leg["type"] == "call" else max(0.0, leg["strike"] - S)
        v += intr if leg["action"] == "BUY" else -intr
    return v


def _wing_width(legs):
    puts = sorted(l["strike"] for l in legs if l["type"] == "put")
    calls = sorted(l["strike"] for l in legs if l["type"] == "call")
    w = []
    if len(puts) >= 2:  w.append(abs(puts[1] - puts[0]))
    if len(calls) >= 2: w.append(abs(calls[-1] - calls[-2]))
    return max(w) if w else 0.0


def simulate(legs, spy_df, dates, entry_idx, vix_at):
    """Price the trade's lifecycle (BS MTM, 70% target / 21-DTE / expiry exit) with
    frictions. Returns {pnl, outcome, capital, max_profit, days_held}."""
    entry_date = dates[entry_idx]
    expiry = entry_date + timedelta(days=ENTRY_DTE)
    spot0 = float(spy_df.loc[entry_date, "close"])
    vix0 = vix_at.get(entry_date, 16.0)
    sigma0 = vix0 / 100.0
    t0 = ENTRY_DTE / 365.0

    nd0 = _net_debit_bs(legs, spot0, sigma0, t0)
    is_credit = nd0 < 0
    width = _wing_width(legs)
    commission = COMMISSION_PER_LEG * len(legs) * 2

    if is_credit:
        credit = -nd0 - ENTRY_SLIPPAGE           # received (less, slipped)
        entry_eff = -credit
        max_profit = credit * 100
        capital = max(0.01, (width - credit)) * 100   # collateral = max loss
    else:
        debit = nd0 + ENTRY_SLIPPAGE             # paid (more, slipped)
        entry_eff = debit
        # long fly max profit = half-width - debit
        halfwidth = abs(legs[1]["strike"] - legs[0]["strike"])
        max_profit = max(0.01, (halfwidth - debit)) * 100
        capital = debit * 100                     # debit IS the max loss

    for j in range(entry_idx + 1, len(dates)):
        d = dates[j]
        dte = (expiry - d).days
        spot = float(spy_df.loc[d, "close"])
        if dte <= 0:
            cur = _value_at_expiry(legs, spot)
        else:
            cur = _net_debit_bs(legs, spot, vix_at.get(d, vix0) / 100.0, max(dte, 0) / 365.0)
        pnl = (cur - entry_eff) * 100 - EXIT_SLIPPAGE * 100   # round-trip slip
        hit_target = max_profit > 0 and pnl / max_profit >= PROFIT_TARGET_PCT
        if hit_target or dte <= DTE_CLOSE_THRESHOLD or dte <= 0:
            net = pnl - commission
            return {
                "pnl": round(net, 2),
                "outcome": "win" if net > 0 else "loss" if net < 0 else "be",
                "capital": round(capital, 2),
                "max_profit": round(max_profit, 2),
                "days_held": (d - entry_date).days,
            }
    return None


def _classify_entries(spy_df, vix_at):
    detector = RegimeDetector()
    dates = sorted(spy_df.index)
    entries = []
    for i, d in enumerate(dates):
        if i < 210 or (ENTRY_DTE / 365.0) and i > len(dates) - 35:
            continue
        hist = spy_df.loc[dates[max(0, i - 250):i + 1]].copy()
        hist.index = pd.to_datetime(hist.index)
        try:
            r = detector.classify(spy_daily_df=hist, vix_current=vix_at.get(d, 16.0),
                                  ivr_current=30.0, today=d)
        except Exception:
            continue
        if r.regime in CONDOR_REGIMES:
            entries.append(i)
    return dates, entries


def run(years=5):
    from backtests.spy_daily_backtest import BacktestDataLoader
    spy_df, vix_df = BacktestDataLoader().load(years=years, source="local")
    spy_df.index = [pd.Timestamp(d).date() for d in spy_df.index]
    vix_at = {}
    if vix_df is not None and len(vix_df):
        vix_at = {pd.Timestamp(d).date(): float(c) for d, c in vix_df["close"].items()}

    dates, entries = _classify_entries(spy_df, vix_at)
    print(f"Entry days (CHOPPY_LOW_VOL/TRANSITION): {len(entries)} over {len(dates)} sessions")

    structs = {
        "condor_2pct": lambda s: condor_legs(s, 0.020),
        "condor_1pct": lambda s: condor_legs(s, 0.010),
        "butterfly":   lambda s: butterfly_legs(s),
    }
    agg = {k: [] for k in structs}
    for i in entries:
        spot = float(spy_df.loc[dates[i], "close"])
        for name, builder in structs.items():
            res = simulate(builder(spot), spy_df, dates, i, vix_at)
            if res:
                agg[name].append(res)

    print(f"\n{'structure':14}{'n':>5}{'win%':>7}{'totP&L':>10}{'avgP&L':>9}"
          f"{'avgCap':>9}{'ret/cap':>9}{'avgDays':>9}")
    rows = []
    for name, trades in agg.items():
        n = len(trades)
        if not n:
            continue
        wins = sum(1 for t in trades if t["outcome"] == "win")
        tot = sum(t["pnl"] for t in trades)
        avg = tot / n
        cap = sum(t["capital"] for t in trades) / n
        roc = (avg / cap * 100) if cap else 0.0
        days = sum(t["days_held"] for t in trades) / n
        rows.append((name, n, wins / n * 100, tot, avg, cap, roc, days))
        print(f"{name:14}{n:>5}{wins/n*100:>6.1f}%{tot:>10.0f}{avg:>9.2f}"
              f"{cap:>9.0f}{roc:>8.1f}%{days:>9.1f}")
    return rows


if __name__ == "__main__":
    run(years=5)
