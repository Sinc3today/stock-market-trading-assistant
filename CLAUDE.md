# CLAUDE.md — Trading Assistant
# Read this at the start of every Claude Code session before touching anything.

## What This Project Is

A modular Python trading bot that scans markets, scores stocks using technical
indicators, recommends SPY options plays based on a regime classifier, and posts
alerts to Discord. Backtested over 5 years with Sharpe 1.73 and 50.3% win rate.

## Current Architecture

```
main.py                     Entry point, scheduler, wires everything together
config.py                   All thresholds, API keys, paths via .env

data/
  polygon_client.py         SPY/stock daily + intraday bars
  alpaca_client.py          Intraday data (free tier)
  vix_client.py             VIX via Polygon or CBOE CSV fallback
  ivr_client.py             IV Rank via options chain or VIX proxy
  event_calendar.py         FOMC, CPI, NFP, OPEX block dates

indicators/
  moving_averages.py        MA20/50/200, stack, distance
  donchian.py               Donchian channels, breakouts
  volume.py                 RVOL, volume direction
  cvd.py                    Cumulative volume delta
  rsi.py                    RSI + divergence

signals/
  scorer.py                 Combines indicators into 0-100 score
  gates.py                  R/R, score, direction, earnings gates
  alert_builder.py          Formats alert dicts
  options_layer.py          Debit spreads, credit spreads, iron condors
  regime_detector.py        6-regime SPY classifier (ADX + VIX + IVR)
  spy_daily_strategy.py     Regime -> OptionsLayer orchestrator

scanners/
  premarket.py              8:00 AM ET gap + volume scan
  swing_scanner.py          9:00 AM ET daily signal scan
  intraday_scanner.py       Every 5 min during market hours
  news_scanner.py           Morning/midday/EOD briefings via Polygon
  economic_scanner.py       FRED data, economic releases
  options_flow_scanner.py   Unusual options activity (VOL/OI spikes)

scheduler/
  spy_daily_scheduler.py    09:15 play / 16:30 close snap / 19:00 reflection

alerts/
  discord_bot.py            Bot, slash commands, post_alert_sync, post_message_sync
  ai_advisor.py             Pre/post trade Claude analysis

journal/
  trade_recorder.py         Logs fills, calculates P&L per strategy type
  lessons.py                Post-trade reflections, pattern tracking
  plan_logger.py            Pre-trade plans (separate from fills)

backtests/
  spy_daily_backtest.py     5-year SPY replay engine
  spy_history.csv           Local yfinance data (5yr)

learning/                   Self-learning loop (paper exec -> reflect -> hypothesize -> backtest)
  knowledge_base.py         JSONL KB at logs/learning/knowledge.jsonl + KNOWLEDGE.md rollup
  predictions.py            One directional prediction per day; rolling accuracy
  paper_broker.py           09:16 ET — auto paper-trade from today's plan, tagged [AUTO-PAPER]
  outcome_resolver.py       16:05 ET — score the prediction, snapshot open paper trades
  reflector.py              19:01 ET — Claude self-reflection -> KB entries + reflection MD
  hypothesis_engine.py      Sat 10:00 — Claude proposes ONE tunable change (whitelisted)
  hypothesis_runner.py      Sat 11:00 — backtest the change, mark accept/reject/inconclusive
  off_hours_learner.py      Sun 10:00 — replay 60d, find near-misses, append KB observations
  scheduler.py              register_learning_jobs() — wires all six jobs onto APScheduler
```

## Self-Learning Loop Rules

11. `[AUTO-PAPER]` in `trade.notes_entry` = bot-generated paper position.
    Never confuse with a real fill; the journal/web app should label these
    distinctly. Size is always 1 contract.
12. Hypothesis proposals are bounded to the whitelist in
    `learning/hypothesis_engine.TUNABLE_PARAMS`. To make a new threshold
    self-tunable, add (module, var, min, max, type) to that dict.
13. An **accepted** hypothesis (`backtest.verdict == "accepted"`) sits in
    `logs/learning/hypotheses/` until a human promotes it. The runner
    never edits source. Promotion is a deliberate step.
14. The Claude calls in `reflector` / `hypothesis_engine` / `off_hours_learner`
    require strict JSON replies. If parsing fails the raw reply is still
    saved to disk so nothing is lost — fix the prompt, don't blame the LLM.
15. Predictions and reflections run *every weekday whether or not the
    user is around*. That's the whole point — don't add gates that
    require human acknowledgement.

## Tuned Thresholds (from backtest — do not change without re-running backtest)

Live values below verified against `signals/regime_detector.py` on 2026-06-06.
(Note: these live in `regime_detector.py`, not `config.py`.)

```python
ADX_TREND_MIN    = 32.0   # 20 → 25 → 30 → 32. Raised to filter weak trends.
VIX_CALM_MAX     = 18.0   # 17 → 18 (2026-05-20). Promotes more days to "calm".
EXTENDED_TREND_MAX_PCT = 9.0   # Skip bull plays when SPY > 9% above 200-MA
                               # (extension gate; under shadow-test measurement)
TRENDING_HIGH_VOL tradeable = False   # Confirmed no edge (19% win rate)
```

**Timeframe routing (live):** all **0DTE** strategies are gated to the
**learning sandbox** via `config.INTRADAY_FEASIBILITY` (prohibitive feasibility
floor) — 0DTE no longer trades in the disciplined/real-money-proxy book (no edge
as designed). **1-3DTE** (iron condors + debit spreads) and the **45DTE** daily
SPY play trade in the **disciplined** book.

## Standing Rules

1. Never run scanners on weekends — check `datetime.now(eastern).weekday() >= 5`
2. Never run intraday scanner outside 9:30-16:00 ET
3. Always use `config.SWING_PRIMARY_TIMEFRAME` not hardcoded `"day"` or `"1day"`
4. Every new module needs a test file in `tests/`
5. Run `pytest tests/ -v -m "not integration" --tb=short` before every commit
6. Never commit with failing tests — fix the code, not the assertions
7. `post_message_sync` for plain Discord messages, `post_alert_sync` for scored alerts
8. `plan_logger.py` for pre-trade plans, `trade_recorder.py` for actual fills — never mix
9. All thresholds in `config.py` — no magic numbers in scanner/signal code
10. Wrap new scheduler jobs in try/except so one failure never crashes the bot

## Key Technical Decisions (see STRATEGY_LOG.md for reasoning)

- Polygon free tier: 501 bars max, use yfinance CSV for backtesting
- VIX: Polygon I:VIX requires paid plan, CBOE CSV is free fallback
- IVR: computed from VIX proxy (VIX IS SPY's 30-day IV)
- Discord race condition fix: `_bot_ready` threading.Event gates all posts
- APScheduler not `schedule` library (already in requirements)
- TRENDING_HIGH_VOL skipped because backtest showed 19% win rate
- Iron condor is the core edge: 74.1% win rate over 5 years

## What's In Progress

See BUILD_LOG.md for latest session changes.
See TRADING_ASSISTANT.md for current project state and decisions.

## Before You Start Any Session

1. Read BUILD_LOG.md — last 2-3 entries
2. Read TRADING_ASSISTANT.md — Active Decisions section
3. Run `pytest tests/ -v -m "not integration" --tb=short` to confirm baseline
4. Check git status — never build on uncommitted changes

## Parking Lot

<!-- vault-router: auto-promote target -->

### From `what-demo-tutorial-should-we-do-together-data-apis` — [VERIFIED] · auto-promoted 2026-06-02
**Insight:** The vault note reviews three financial APIs (Robin Stocks, Alpaca Markets, Public API) directly applicable to the trading bot's data ingestion layer; Alpaca is already used for intraday data, and Robin Stocks or Public API could provide alternative stock/options feeds for the scorer and gates modules.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-what-demo-tutorial-should-we-do-together-data-apis.md) · creator: mar_antaya
**Confidence:** 0.9
<!-- vault-router: hash=ca7480e9 source=what-demo-tutorial-should-we-do-together-data-apis -->


(vault-router will insert auto-promoted ideas here. Manual additions welcome above or below.)
