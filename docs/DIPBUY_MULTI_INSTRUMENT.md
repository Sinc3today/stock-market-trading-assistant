# Dip-buy multi-instrument study (QQQ / IWM)

**Question (user, 2026-07-09):** expand beyond SPY carefully — does the WF-
validated oversold dip-buy (RSI<30 → bull debit, parameter-free) work on QQQ/IWM?
It's a debit structure (no collateral), so passing instruments add trade
frequency without consuming buying power.

**Method:** `backtests/dipbuy_multi_instrument.py` — the EXACT SPY pipeline
(same signal fns, same option pricer, same expanding-window OOS folds and
verdict gates), run per instrument on yfinance history from 2010. Nothing fit
per instrument — pure OOS transfer of a fixed rule.

| ticker | trades | OOS n | OOS win% | OOS avg P&L | sharpe | pos years | verdict |
|---|---|---|---|---|---|---|---|
| SPY | 31 | 25 | 76.0% | +$234.59 | 0.891 | 89% | **PASS** (baseline reconfirmed) |
| **QQQ** | 29 | 22 | **81.8%** | **+$211.41** | **0.928** | 82% | **PASS** |
| IWM | 36 | 29 | 44.8% | −$3.36 | −0.025 | 56% | **FAIL** |

**Read:**
- **QQQ passes the same gates that greenlit the SPY dip-buy** — high win rate,
  positive nearly every OOS year, consistent across 11 fold-years. Large-cap
  index mean-reversion transfers.
- **IWM cleanly fails** — small caps don't mean-revert off RSI<30 the same way.
  Good falsification; do not trade it.
- Caveat: sigma=VIX (SPY's vol) for all pricing — QQQ debits understated
  (~1.15-1.3x realized-vol gap), so trust the hit-rate/consistency over the
  absolute P&L. Real QQQ debits will be somewhat higher; edge direction stands.

**Deployment path (careful-first, per user):** paper-trade QQQ dip-buys in the
learning flow before any live mirroring — accumulate real-priced paper fills for
a few weeks, then promote if live-consistent. NOT yet wired into the scanner.
