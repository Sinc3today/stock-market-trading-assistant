# TRADING_ASSISTANT.md — Living State Document
# Updated by: Claude.ai (strategy) and Claude Code (after build sessions)
# Last updated: 2026-04-30

---

## Project State

### What's Built and Working
- Signal engine: MA, Donchian, Volume, CVD, RSI indicators
- Scorer: 0-100 score with trend/setup/volume layers + confluence bonus
- Gates: R/R, score minimum, direction, earnings block
- Options layer: debit spreads, credit spreads, iron condors
- SPY daily regime strategy: 6-regime classifier (ADX + VIX + IVR)
- Backtest harness: 5-year replay, Sharpe 1.73, 50.3% win rate
- Data clients: Polygon, Alpaca (intraday), VIX (CBOE fallback), IVR (VIX proxy)
- Event calendar: FOMC, CPI, NFP, OPEX auto-computed + cached
- Scanners: premarket, swing, intraday, news, economic, options flow
- Scheduler: 09:15 ET play / 16:30 close snap / 19:00 reflection
- Discord: race condition fixed, post_message_sync + post_alert_sync working
- Journal: trade recorder, lessons, plan logger
- Tests: 157+ passing (unit tests, no API calls required for core suite)

### Tuned Parameters (backtest-validated)
- ADX_TREND_MIN = 25 (raised from 20)
- VIX_CALM_MAX = 17 (tightened from 18)
- TRENDING_HIGH_VOL = not tradeable (19% win rate confirmed useless)
- Iron condor win rate: 74.1% — core edge of the strategy

### Known Issues
- Weekend guard missing on swing scanner and intraday scanner (fires on Saturdays)
- IntervalTrigger fires immediately on startup before market open
- Discord alerts sometimes delayed vs real-time needs
- Polygon free plan: 501 bar limit caps backtest to ~14 months via API
  (workaround: yfinance CSV download for full 5-year backtest)

---

## Active Decisions

### In Flight
- Push notifications: moving from Discord to Pushover (or Telegram) for
  time-sensitive alerts. Discord stays as secondary log.
  Status: design decided, not yet built.

- Weekend/market hours fix: swing scanner needs weekday guard,
  intraday scanner needs weekday check in is_market_hours(),
  IntervalTrigger needs start_date=next_open.
  Status: fix written, not yet applied.

- Options flow scanner: UOA scanner built and tested (18/18 tests),
  needs wiring into main.py scheduler at 9:35 AM ET.
  Status: file ready to drop in.

- Claude Code cleanup pass: remove duplicate imports in main.py,
  add missing __init__.py files, standardize timeframe strings.
  Status: prompt written, not yet run.

### Recently Decided (see STRATEGY_LOG.md for reasoning)
- Paper trade for 30 days before going live with real money
- Backtest confirms strategy viable: commit one contract per signal
- Iron condor is the primary edge — prioritize condor setup quality over quantity
- Bear debit (-$970 over 5yr) acceptable drag, not urgent to fix
- Pushover > Discord for mobile alerts due to delivery reliability

---

## Parking Lot

### Not Started — Keep These Visible
- Push notification system (Pushover/Telegram) replacing Discord as primary
- Position sizing module: how many contracts based on account size + max daily risk
- Dashboard page: today's SPY regime + play recommendation (Streamlit)
- Automated paper trade logger: records each play without manual entry
- Nexus integration: query nexus.db by category for macro/strategy context
  (categories: Stock Market, Economics, Trading Strategies, World News)
- Real-time market data upgrade (Polygon paid plan or alternative)
- Trade execution layer: paper trading first, then live via Alpaca
- Risk management rules: max daily loss, max open positions, position sizing
- Unified dashboard: Trading Assistant + Nexus + future projects
- Cloud deployment: Railway (planned), currently Windows local only
- Home server deployment: Ubuntu + Docker
- Rack server for production
- Backtest: run --tune grid search with full 5-year data
- Test for options flow scanner against live Polygon options data
- CLAUDE.md /init run to let Claude Code index codebase properly
