# CLAUDE.md — Trading Assistant
# Read this at the start of every Claude Code session before touching anything.

## What This Project Is

A modular Python trading bot that scans markets, scores stocks using technical
indicators, recommends SPY options plays based on a regime classifier, and posts
alerts to Discord. Backtested over 5 years with Sharpe 1.73 and 50.3% win rate.

## Plain-English: what went wrong, and what we're doing about it (2026-09-07)

*Written so a non-engineer can follow it. If you only read one section, read this.*

### The short version

On 2026-09-06 we found 8 bugs in a single day. That sounds like the safety
checks working. It isn't — **8 bugs in a day means the system is unreliable**,
and two of those bugs were in code written that same day. So we stopped and
audited the whole thing.

**The system isn't dangerous — it's untrustworthy as a measuring instrument.**
Only two real-money positions exist. But nearly every number it reports has
been suspect, and we make strategy decisions from those numbers.

### The one root cause

> **Nothing in this codebase has a single owner, and nothing can list all the
> places a decision gets made.**

An analogy. Imagine five people each keeping their own copy of the company
price list. Someone updates a price — but only on the copies they happen to
open. Now the copies disagree, and *nobody can tell*, because no one has a
list of who holds a copy.

That is literally what happened. Proof, all measured the same day:

- I fixed "which strategies are credit vs debit" and announced it solved.
  **I'd fixed 3 of 13 copies.**
- `ENFORCE_CONCENTRATION_GUARD = True` sits in the config. It's actually
  enforced in **1 of 6** places that can open a trade.
- There are **6 different ways** the code calculates "how long until this
  option expires."

### The three bug families, with real examples

**1. A guess gets written down as a fact.**

The biggest one. When the code can't find a real price, it substitutes a
plausible number — and that fake number gets *saved to the trade journal*,
where it's indistinguishable from a real one.

> **Example:** `paper_broker` couldn't read the price of a spread, so it used a
> hardcoded **$1.00**. That $1.00 became the recorded entry price on **18
> trades**. One of them shows `max_profit = $200`, which means the real credit
> was about **$2.00** — so its recorded profit is off by ~$100.
>
> **Why it matters:** correcting for this, the *candidate* book goes from a
> reported **+$1,503 profit to roughly −$186 — it flips from winning to
> losing.** That's the book where every strategy is earning its way toward real
> money.

The fix is a principle: **a missing price is not a price.** If we don't know,
say "I don't know" and refuse to trade — never invent a number.

**2. The same rule written in several places, which then drift apart.**

> **Example:** a broken-wing butterfly is a "credit" structure. One file knows
> that. `expiry_resolver.py` doesn't — so at expiry it would record your **best
> outcome as a $350 loss, and your worst outcome as a $150 win.** Exactly
> backwards. 17 positions are currently exposed to it.

**3. Two things that look the same but aren't (units).**

> **Example:** `DTE` means *calendar* days, but the price data is in *trading*
> days. A study walked "7 days" forward through 7 rows of data — which is
> actually ~10 calendar days, i.e. **past the expiry date**. It produced
> confident, published, wrong numbers. Twice.
>
> **Example:** we label a trade "7DTE" but the actual contract we buy expires
> **8.7 days out on average** — because the code prices it as 7 days but then
> picks the nearest Friday. A Monday entry starts **35% of max profit in the
> hole** before the market moves at all.

### Why the tests didn't catch any of it

We have 1,624 tests and they caught **zero** of the 8 bugs. Not because there
are too few — because of what they test.

The tests check one function at a time, with its neighbours *faked out*. So
**two parts of the system disagreeing is not something a test can notice.**
Every one of the 8 bugs lived exactly there: between two pieces, not inside one.

### What survived (the good news)

- **The iron condor edge is real.** The disciplined book — our real-money proxy
  — actually got *better* under audit: **76% win rate, +$1,441**.
- "0DTE has no edge" and the 74% condor win rate both hold up.
- No mis-scaled volatility, no double-counted contract multipliers, the
  scheduler is correctly timezone-pinned, every network call has a timeout.

### What we're doing about it — the 4 phases

Full detail in `docs/SYSTEM_HARDENING_PLAN.md`.

| Phase | Plain description |
|---|---|
| **0 — Stop the bleeding** | Stop writing guesses into the journal. Refuse to trade when a price is unknown. Put a lock on the trade file (4 jobs write it at once with no lock today). Make failures visible — a whole trading day can currently be skipped silently. |
| **1 — One owner per decision** | Collapse the 13 copies into 1. One place that knows credit-vs-debit, one that values a spread, one that decides "may I open a trade" so the risk limits apply everywhere automatically. |
| **2 — Make partial fixes impossible** | Add tests that *enumerate*: "every strategy name anything can produce is understood by everything that consumes it." This is what makes "I fixed 3 of 13" impossible to repeat. |
| **3 — Subtract** | Delete what isn't earning its keep. 0DTE is shelved but still burns API calls and writes records. A weekly job runs a full backtest for a disabled feature. |

**The rule going forward: we don't get confident by finding bugs faster. We
get confident by removing the places bugs can hide.**

---

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
5. Run the full suite before every commit. Parallel (recommended, ~90s on 16 cores):
   `pytest tests/ -m "not integration" -n auto --dist loadfile --tb=short`
   (`--dist loadfile` keeps each file on one worker, preserving module fixtures/ordering.
   For debugging a single test, drop the `-n` flags to run serially with pdb.)
6. Never commit with failing tests — fix the code, not the assertions
7. `post_message_sync` for plain Discord messages, `post_alert_sync` for scored alerts
8. `plan_logger.py` for pre-trade plans, `trade_recorder.py` for actual fills — never mix
9. All thresholds in `config.py` — no magic numbers in scanner/signal code
10. Wrap new scheduler jobs in try/except so one failure never crashes the bot
11. **Never OPEN a position outside the entry window** — `config.within_entry_window()`
    gates 09:45–15:00 ET (no opens in the first 15 min after the bell or the last
    hour before close). Every open path must call it (paper_broker, dipbuy_forward,
    intraday). EXITS/management are NOT gated — you must always be able to close.

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
3. Run `pytest tests/ -m "not integration" -n auto --dist loadfile --tb=short` to confirm baseline
4. Check git status — never build on uncommitted changes

## Parking Lot

### [MANUAL] xDTE + Regime explainer course — walk it together
A guided session, one rung and one regime at a time, where we look at each and
decide **out loud** whether it earns its place. Not a doc to read — a
conversation with the data open.

Rungs to cover: 0DTE (shelved), 1-3DTE, 7DTE, 14DTE, 21DTE, 30DTE, 45DTE.
Regimes: choppy_low_vol, choppy_transition, choppy_high_vol, trending_up_calm,
trending_high_vol, event_day.

For each, the same four questions:
1. What is the *mechanical* reason an edge would exist here (theta? mean
   reversion? skew?) — not "the backtest says so."
2. What does the live record say, and is that record trustworthy after the
   2026-09-07 audit?
3. What would make us abandon it? (pre-registered, before we look)
4. Is it worth the attention it costs, given everything else running?

**Why it matters:** we currently run four condor rungs and two BWB rungs
largely because studies said so, and the audit showed several of those studies
were measured with a contaminated instrument. This is the session where we
decide what we actually believe.

### [MANUAL] Full code review — functionality, discipline, relevancy
Module-by-module pass over the whole tree asking three questions each:
**does it work, does it follow the standing rules, and is it still needed?**

Distinct from the 2026-09-07 audit, which hunted specific defect classes. This
is the "should this exist at all" pass. Known starting material: 30 of 180
modules have zero test references; 21 of those are in `backtests/`. Seven
modules have no production importer at all (`wf_common`, `order_mapper`,
`regime_lens`, `context_analyst`, `rh_session`, `lessons`, `performance`).
0DTE is shelved but still fetches option chains and writes journal records.

### [MANUAL] Re-run the futures / overnight study on a verified instrument
`docs/GAP_CONDITIONAL_STUDY.md` §C (2026-07-11) concluded: Sunday 10 PM ES
sentinel keeps its place (0.75 corr, 82% of big gaps), weeknight sentinels
rejected (40–61% catch → false confidence). **That study predates the audit**,
and the standing rule from 2026-09-07 is that findings from a contaminated
instrument get re-verified rather than inherited.

Also worth extending while we are in there: the original tested **gap
prediction only**. Whether overnight / Asia / Europe action is useful as a
**regime or trend input** has never been tested, and the BoA positioning note
(2026-09-07) makes the flow angle more interesting than it looked in July.

### [MANUAL] Tradier auto-execution migration — pre-built, PARKED
Full transition plan documented in `docs/TRADIER_MIGRATION.md` (2026-07-18):
account/options-level prereqs, personal-access-token auth (no partner OAuth
needed for one account), multileg order mapping from `build_condor()` legs → OCC
symbols, a `brokers/` abstraction mirroring rh_sync's guard model (sandbox-first
`TRADIER_LIVE` flag, preview-parity gate, reuse of existing entry-window/pacing/
concentration gates), and a 3-phase rollout (sandbox → human-approve → full
auto). **Gate: do not build until a real-fill sleeve proves the live edge
(~20–30 closed trades holding the paper win rate through a regime change).** As
of 2026-07-18 we have 1 real fill — nowhere near.

<!-- vault-router: auto-promote target -->

### From `replying-to-heizelevelina-jim-rohn-academy-jimrohn` — [OPINION] · auto-promoted 2026-06-21
**Insight:** Jim Rohn's thesis that personal development and self-improvement drive financial results aligns with the trading bot's philosophy of continuous indicator refinement and regime adaptation; the note's emphasis on 'becoming twice as valuable' mirrors iterative signal scoring improvements.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-replying-to-heizelevelina-jim-rohn-academy-jimrohn.md) · creator: diamakis
**Confidence:** 0.35
<!-- vault-router: hash=17bc0e91 source=replying-to-heizelevelina-jim-rohn-academy-jimrohn -->


### From `the-4-ifs-that-make-life-worthwhile-jim-rohn-fypp` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The motivational framework of 'learn, try, stay, share' directly parallels disciplined trading psychology: continuous learning (market study), trying (backtesting and live trades), staying (patience through drawdowns), and sharing (community signals via Discord alerts).
**Source:** [Nexus note](Nexus/2026/05/2026-05-13-the-4-ifs-that-make-life-worthwhile-jim-rohn-fypp.md) · creator: doc..jude
**Confidence:** 0.4
<!-- vault-router: hash=b68fea7f source=the-4-ifs-that-make-life-worthwhile-jim-rohn-fypp -->


### From `282k-views-47k-reactions-the-233-year-monopoly-of` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The note's thesis about Wall Street infrastructure replacement via blockchain and tokenized assets is directly relevant to the trading bot's market regime classification and options scoring logic—if equity settlement moves to blockchain within 12-36 months, the bot's technical indicators and risk models may need recalibration for novel liquidity/volatility patterns.
**Source:** [Nexus note](Nexus/-282k-views-47k-reactions-the-233-year-monopoly-of.md) · creator: Alexanderoncrypto
**Confidence:** 0.4
<!-- vault-router: hash=7b1b93c7 source=282k-views-47k-reactions-the-233-year-monopoly-of -->


### From `on-wanting-to-do-everything-but-actually-doing` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The note's dopamine-planning cycle ('backtested perfectly, feeling productive') vs. actual trading execution mirrors the trading bot's challenge: high Sharpe in backtest, but real alerts may stall if scorer output creates decision paralysis (too many conflicting signals, over-rumination of edge cases, or comfort with not trading). Apply stuck-types framework to alert-publishing logic.
**Source:** [Nexus note](Nexus/2026/05/2026-05-22-on-wanting-to-do-everything-but-actually-doing.md) · creator: lindsiann
**Confidence:** 0.5
<!-- vault-router: hash=b1c186bd source=on-wanting-to-do-everything-but-actually-doing -->


### From `open-sourced-project-for-one-person-companies-that` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The multi-agent orchestration pattern from gstack — simulating a full trading team (risk manager, technical analyst, sentiment analyst) — could enhance the trading bot's signal generation and risk-gating logic.
**Source:** [Nexus note](Nexus/2026/05/2026-05-14-open-sourced-project-for-one-person-companies-that.md) · creator: brandnat
**Confidence:** 0.5
<!-- vault-router: hash=63c3a069 source=open-sourced-project-for-one-person-companies-that -->


### From `is-markdown-cooked` — [OPINION] · auto-promoted 2026-06-21
**Insight:** Interactive HTML outputs with slider controls for regime thresholds, score gates, and R/R parameters could let traders fine-tune bot settings in real-time before posting Discord alerts, avoiding multiple backtests.
**Source:** [Nexus note](Nexus/2026/05/2026-05-14-is-markdown-cooked.md) · creator: wutronic
**Confidence:** 0.5
<!-- vault-router: hash=b6a4d545 source=is-markdown-cooked -->


### From `the-most-valuable-person-in-any-room-right-now-the` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The trading bot benefits from someone bridging quantitative signal design (technical), market microstructure (business domain), and live-trading risk management (implementation); the note frames this hybrid skill as increasingly valuable for 2026 fintech roles.
**Source:** [Nexus note](Nexus/2026/05/2026-05-13-the-most-valuable-person-in-any-room-right-now-the.md) · creator: nate.b.jones
**Confidence:** 0.5
<!-- vault-router: hash=0bec5dba source=the-most-valuable-person-in-any-room-right-now-the -->


### From `claude-agent-view` — [VERIFIED] · auto-promoted 2026-06-21
**Insight:** Claude Code's Agents View could streamline multi-timeframe signal monitoring—launch separate Claude agents to track SPY technicals, VIX regime, and IV Rank in parallel, peek into each via spacebar to review alerts without context-switching, and background sessions when they're working.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-claude-agent-view.md) · creator: chase_ai_
**Confidence:** 0.5
<!-- vault-router: hash=53418bd2 source=claude-agent-view -->


### From `ponytail-claude-code` — [MOSTLY TRUE] · auto-promoted 2026-06-21
**Insight:** Ponytail's token optimization could reduce costs in the scorer and gates logic that processes intraday indicator data; benchmark current Claude API spend.
**Source:** [Nexus note](Nexus/-ponytail-claude-code.md) · creator: chase_ai_
**Confidence:** 0.5
<!-- vault-router: hash=e607d591 source=ponytail-claude-code -->


### From `i-built-a-private-hyperliquid-trading-desk-made-of` — [UNVERIFIED] · auto-promoted 2026-06-21
**Insight:** The multi-agent trading system with parallel strategy execution (momentum, funding rate, scalping) mirrors the modular scorer + gates architecture; study agent coordination patterns and Discord alert routing for multi-strategy regime classification.
**Source:** [Nexus note](Nexus/2026/06/2026-06-01-i-built-a-private-hyperliquid-trading-desk-made-of.md) · creator: mayurcdev
**Confidence:** 0.65
<!-- vault-router: hash=63021523 source=i-built-a-private-hyperliquid-trading-desk-made-of -->


### From `stops-are-not-just-for-exits-a-stop-loss-manages-w` — [MOSTLY TRUE] · auto-promoted 2026-06-21
**Insight:** The disciplined breakout entry method (buy stop above resistance + candle close confirmation) and trailing stop risk management align directly with the bot's signal gates and position management logic; incorporate these entry/exit rules into the scorer or gates module.
**Source:** [Nexus note](Nexus/-stops-are-not-just-for-exits-a-stop-loss-manages-w.md) · creator: nicholas_crown
**Confidence:** 0.7
<!-- vault-router: hash=0d3c1375 source=stops-are-not-just-for-exits-a-stop-loss-manages-w -->


### From `this-guy-found-out-how-to-stop-claude-lying-with-a` — [VERIFIED] · auto-promoted 2026-06-21
**Insight:** Apply the Council technique to the scorer and gates logic to generate multiple analytical personas (Contrarian, First Principles, Expansionist, Outsider) evaluating SPY regime and trade confidence, reducing single-pass bias in options recommendations before Discord alerts.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-this-guy-found-out-how-to-stop-claude-lying-with-a.md) · creator: nicksadler.io
**Confidence:** 0.7
<!-- vault-router: hash=f0523856 source=this-guy-found-out-how-to-stop-claude-lying-with-a -->


### From `all-traders-read-charts-the-ones-who-read-gamma-ac` — [MOSTLY TRUE] · auto-promoted 2026-06-21
**Insight:** Integrate Gamma Exposure (GEX) as a new indicator module in the signals pipeline—GEX can identify key support/resistance levels and regime shifts (long vs. short gamma), complementing existing technical scores for SPY options alerts.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-all-traders-read-charts-the-ones-who-read-gamma-ac.md) · creator: nicholas_crown
**Confidence:** 0.7
<!-- vault-router: hash=7de33913 source=all-traders-read-charts-the-ones-who-read-gamma-ac -->


### From `my-claude-folder-has-caught-more-mistakes-than-h` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The custom slash commands and specialist agent pattern (race-condition hunter, security reviewer, documentation flagger) directly parallels the modular signal/gate architecture; apply DevSwarm's domain-split concept to isolate technical indicators, regime classifiers, and risk gates as independent agents that run in parallel for faster signal iteration.
**Source:** [Nexus note](Nexus/2026/05/2026-05-11-my-claude-folder-has-caught-more-mistakes-than-h.md) · creator: codenameposhan
**Confidence:** 0.7
<!-- vault-router: hash=07f00aae source=my-claude-folder-has-caught-more-mistakes-than-h -->


### From `your-401k-is-their-exit-strategy-news-us-andrei` — [MOSTLY TRUE] · auto-promoted 2026-06-21
**Insight:** The note's analysis of index fund mechanics forcing passive capital into overvalued IPOs directly informs risk modeling for options strategies; incorporate IPO-bubble regime detection and index-rebalancing liquidity shocks into the regime classifier.
**Source:** [Nexus note](Nexus/-your-401k-is-their-exit-strategy-news-us-andrei.md) · creator: user360378651471
**Confidence:** 0.7
<!-- vault-router: hash=44c117a5 source=your-401k-is-their-exit-strategy-news-us-andrei -->


### From `the-way-we-protect-ourselves-from-a-market-crash-a` — [OPINION] · auto-promoted 2026-06-21
**Insight:** The note's sector rotation strategy and options surface analysis directly inform the trading bot's signal generation; cap-weighted vs. equal-weight divergence could be a new regime classifier gate.
**Source:** [Nexus note](Nexus/-the-way-we-protect-ourselves-from-a-market-crash-a.md) · creator: nicholas_crown
**Confidence:** 0.7
<!-- vault-router: hash=888d7ea3 source=the-way-we-protect-ourselves-from-a-market-crash-a -->


### From `this-is-the-layer-that-took-me-years-to-learn-the` — [MOSTLY TRUE] · auto-promoted 2026-06-21
**Insight:** The note's emphasis on positioning data (CFTC COT, Goldman Sachs prime brokerage, dealer gamma) as leading indicators aligns directly with the trading bot's technical indicator and regime classifier approach; could integrate COT data as a market regime feature.
**Source:** [Nexus note](Nexus/-this-is-the-layer-that-took-me-years-to-learn-the.md) · creator: nicholas_crown
**Confidence:** 0.7
<!-- vault-router: hash=36a9f396 source=this-is-the-layer-that-took-me-years-to-learn-the -->


### From `the-central-debate-on-wall-street-is-starting-to` — [MOSTLY TRUE] · auto-promoted 2026-06-07
**Insight:** The note's analysis of market concentration (semiconductors at 18% of S&P 500, AI stocks near 50%), valuations, and sector momentum directly informs the trading bot's regime classifier and scoring thresholds; compare 1999 bubble indicators against current technical setups to refine entry/exit gates.
**Source:** [Nexus note](Nexus/2026/05/2026-05-13-the-central-debate-on-wall-street-is-starting-to.md) · creator: cnbc
**Confidence:** 0.75
<!-- vault-router: hash=8cd204d9 source=the-central-debate-on-wall-street-is-starting-to -->


### From `this-kid-is-outtrading-you-stocks-daytrader` — [MOSTLY TRUE] · auto-promoted 2026-06-07
**Insight:** The note documents a retail trader using Bollinger Bands + RSI strategy with live results and commentary on strategy effectiveness; directly applicable to backtesting new technical indicator combinations and validating signal quality against real market outcomes.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-this-kid-is-outtrading-you-stocks-daytrader.md) · creator: thestockguy
**Confidence:** 0.8
<!-- vault-router: hash=1175abc8 source=this-kid-is-outtrading-you-stocks-daytrader -->


### From `what-demo-tutorial-should-we-do-together-data-apis` — [VERIFIED] · auto-promoted 2026-06-02
**Insight:** The vault note reviews three financial APIs (Robin Stocks, Alpaca Markets, Public API) directly applicable to the trading bot's data ingestion layer; Alpaca is already used for intraday data, and Robin Stocks or Public API could provide alternative stock/options feeds for the scorer and gates modules.
**Source:** [Nexus note](Nexus/2026/05/2026-05-12-what-demo-tutorial-should-we-do-together-data-apis.md) · creator: mar_antaya
**Confidence:** 0.9
<!-- vault-router: hash=ca7480e9 source=what-demo-tutorial-should-we-do-together-data-apis -->


(vault-router will insert auto-promoted ideas here. Manual additions welcome above or below.)
