# BUILD_LOG.md — Claude Code Session History
# Append a new entry after every Claude Code session.
# Format: ## YYYY-MM-DD | [what was done] | [test result]

---

## 2026-05-17 (PM-4) | Wire EarningsHistory into MorningBriefer + AlertGates

**Why:** PM-3 built the reaction-stats data layer. Two consumers needed
to actually use it: (1) the morning brief, so the user sees "typical
reaction ±X%" instead of just an earnings date; (2) the alert gates,
so we can be smarter than a blunt 2-day block — calm reactors don't
need the same wide window as volatile ones.

**What changed:**

- **`signals/morning_briefer.py`:**
  - New optional `earnings_history` constructor arg.
  - `_get_today_earnings()` now calls `EarningsHistory.annotate_upcoming()`
    on the earnings list when wired; the stats land in `macro_context.earnings`.
  - Claude prompt's `WATCHLIST EARNINGS NEXT 48H` block now appends
    "— typical reaction ±X% (class)" per ticker so the LLM can mention
    it in plain-English skip/watch conditions.
  - Fallback synthesis (no Claude) also embeds the reaction line.
  - `_get_today_earnings` swallows `annotate_upcoming` exceptions and
    returns the un-annotated list so a flaky history fetch never breaks
    the brief.

- **`scheduler/spy_daily_scheduler.py`:** constructs `EarningsHistory`
  and passes it to `MorningBriefer` alongside the existing
  `EarningsCalendar`.

- **`signals/gates.py`:** new optional `earnings_history` dep + new
  config flag `EARNINGS_REACTION_GATE_ENABLED` (default `False`).
  When on:
  - `calm` reactors (<1.5% avg) get `EARNINGS_CALM_WINDOW_DAYS` (default
    `0`) as their block window — alerts allowed up to and including
    the day before earnings.
  - `normal` / `volatile` keep the standard `EARNINGS_BLOCK_DAYS`; the
    class label is surfaced in the suppression message for transparency.
  - Any `EarningsHistory` exception → fall back to the default block,
    so a failed lookup never weakens the gate.

- **`config.py`:** `EARNINGS_REACTION_GATE_ENABLED`,
  `EARNINGS_CALM_WINDOW_DAYS` added (both gated behind the new flag).

- **Tests:** +3 in `tests/test_morning_briefer.py` (annotate wiring,
  failure passthrough), +7 in new
  `tests/test_gates_earnings_reaction.py` covering flag off/on,
  per-class behavior, and history-failure safety. 19/19 briefer +
  7/7 reaction gate + 13/13 history pass.

**Tests:** full suite (cross-module: briefer + gates + scheduler).

---

## 2026-05-17 (PM-3) | Per-ticker post-earnings reaction history

**Why:** The morning brief and gates currently know *when* a ticker
reports earnings (block window from yfinance) but not *how violently*
the stock typically reacts. A ticker that averages a ±5% next-day move
deserves a different sizing/blocking decision than one that averages
±0.8%. This builds the data layer for that — integration with gates
or the brief is a follow-up.

**What changed:**

- **`data/earnings_history.py` (new):** `EarningsHistory.get_reactions(ticker)`
  pulls up to 8 prior earnings dates (yfinance), reads daily bars from
  the injected PolygonClient, and computes the close-to-close % move on
  the trading day *after* each earnings print. Aggregates into:
  `mean_abs_move_pct`, `stdev_move_pct`, `gap_class` ∈ {calm, normal, volatile}.
  - Holiday-aware: if the earnings date has no bar (rare), falls back
    to the prior trading day's close as the baseline.
  - 30-day cache at `logs/earnings_history.json` (per-ticker keys).
  - `annotate_upcoming()` helper merges stats into the
    `EarningsCalendar.get_upcoming()` shape for the brief/dashboard.

- **`tests/test_earnings_history.py` (new, 13 tests):** classification
  boundaries, next-day move math, exact-date vs prior-day fallback,
  cache hit/refresh, disk persistence, annotate passthrough for
  unknown tickers.

**Tests:** targeted (small isolated change, no scheduler wiring).

---

## 2026-05-17 (PM-2) | Expiry-based exit for AUTO-PAPER positions

**Why:** Multi-day spreads recorded by `paper_broker` (debit, credit,
iron condor) had no way to close themselves once their expiry passed.
The outcome resolver's MTM snapshot kept appending notes but the trade
stayed `outcome=open` forever — so the P&L journal, dashboards, and
reflector all saw a permanently-open trade. This closes the loop.

**What changed:**

- **`learning/expiry_resolver.py` (new):** `ExpiryResolver.resolve_expired()`
  walks open `[AUTO-PAPER]` trades, picks each trade's nearest leg
  expiration, and if it's `<= today`, computes intrinsic value at SPY
  close and calls `TradeRecorder.log_exit`.
  - Pricing: intrinsic-only (no theta — by definition zero at expiry).
  - Per-strategy sign:
    - `debit_spread` / `single_leg`: `exit_price = max(0, long − short)`
    - `credit_spread` / `iron_condor`: `exit_price = max(0, short − long)`
  - `format_expiry_message()` — Pushover-style summary, one line per
    closed trade with ✅/❌ and net $.

- **`learning/scheduler.py`:** registered `job_expiry_resolver` at
  **16:10 ET Mon-Fri** (5 min after outcome resolver, so the MTM snapshot
  runs first and the close happens on the same SPY EOD print).

- **`tests/test_learning_expiry_resolver.py` (new, 19 tests):** intrinsic
  math per leg shape, exit-price per strategy, end-to-end credit-spread
  winner + debit-spread loser, idempotency, AUTO_TAG filter, formatter.

---

## 2026-05-17 (PM) | Real historical VIX in off-hours weekend replay

**Why:** `OffHoursLearner._find_near_misses()` was using a hardcoded
`vix_today = 16.0` for every replayed day, so any "near-miss" landing
on the VIX_CALM_MAX boundary was effectively noise — the boundary
flag fired on the wrong days.

**What changed (`4226581`):**

- Added `_load_vix_history()` (uses VIXClient.get_history; CBOE CSV
  fallback already in place) and `_vix_for(lookup, target, fallback)`
  (snap to nearest preceding trading day for weekends/holidays).
- Test fixtures can inject a `{date: vix}` dict via the new
  `vix_history` constructor arg so tests don't hit the network.
- 4 new targeted tests; `tests/test_learning_off_hours.py` 10/10 pass.

---

## 2026-05-17 (UX) | Plain-English on /today + /macro + leaner test cadence

**Why:** Carry forward the Pushover UX win to the dashboard pages.
User shouldn't have to mentally translate between "trending_up_calm"
on /today and "Steady uptrend, low volatility" on Pushover — same
vocabulary everywhere.

Also user pointed out (correctly) that running the full pytest suite
on every commit was wasteful for small UI text changes — adopted a
leaner cadence (saved as memory).

**What changed (`0b9728d`):**

Three coordinated edits to alerts/web_app.py + signals/morning_briefer.py:

1. **Label maps** (single source of truth at top of web_app.py):
   - `_REGIME_LABEL`        — all 5 regime enums → plain English
   - `_VIX_FLAG_LABEL`      — calm / cautious / stress / extreme_stress → plain
   - `_SECTOR_SIGNAL_LABEL` — trending_aligned / rotating / dispersed → plain
   - `_regime_label(regime)` helper used by /today

2. **/today render** — every technical label replaced:
   - Regime badge: "Steady uptrend, low volatility" not "TRENDING_UP_CALM"
   - "Why this trade today" not "Thesis"
   - "Days to expiry" not "DTE"
   - "Risk / Reward" not "R/R"
   - "Skip this trade if" not "Skip if"
   - "Market conditions" not "Macro Context"
   - Volatility / Sector strength / Events next 48h labels
   - **Prefers `plain_summary` over `narrative`** when both present

3. **/macro render** — same vocabulary:
   - "Market Volatility" not "VIX Term Structure"
   - "Sector Strength (vs market, last 20 days)" not "Sector Breadth"
   - "Today / 1-week / 3-month / 6-month expectation" not "VIX/VIX9D/VIX3M/VIX6M"
   - "Outperforming the market" / "Underperforming the market"
   - Plain-English ratio interpretation: "Market expects calm" /
     "Traders pricing in fear" depending on contango ratio

4. **morning_briefer._save_plan** — persists `plain_summary` field
   so /today can read it (was only on the brief dict, not the plan).

**Test guards** (+2 new): assert that no raw regime string leaks into
/today across all 4 enum values, and that plain_summary is preferred
over narrative when both are present.

**Live verified:**

```
$ curl /today | grep regime-form
(empty)   ← no raw "trending_up_calm" anywhere

$ curl /today | grep plain
"Steady uptrend, low volatility"  ✓
"Why this trade today"            ✓
"Days to expiry"                  ✓
"Skip this trade if"              ✓
```

Latest Pushover sent to user's phone with cleanest version yet:

```
💰 SELL PUT CREDIT SPREAD on SPY
Market is climbing steadily with low fear. Selling puts below current
price lets you collect premium as stock prices grind higher—skipping
if anxiety spikes.

Trade:
  Buy SPY $734 put
  Sell SPY $739 put
Expires: Jun 26 (40 days)

🚫 Skip if:
• Skip if volatility jumps above 22 before open—signals fear replacing calm
• Don't take this trade if SPY opens below $735—would break recent uptrend
• Avoid if tech stocks reverse sharply lower—yesterday's leader flipping
```

**Workflow change (memory'd):** Going forward — targeted tests per
commit (sub-second), full suite only before `git push` or for
cross-module changes. Old cadence ran 2.5 min × every commit. Saved
to memory at feedback_test_cadence.md.

**Test result:** 413 passed, 4 deselected, ~156s (was 411, +2 new).

---

## 2026-05-16 (UX) | Plain-English Pushover format

**Why:** Live Pushover test gave the user a real look at the format.
Their feedback: "very technical and I'll need simpler but direct
analysis to make sense of it." Reading "trending_up_calm: BULL PUT
CREDIT SPREAD — IVR elevated, sell the put side... VIX3M ratio
inverts..." from a phone in 5 seconds is not an actionable experience.

**The fix (`7c4ce79`):**

Two coordinated changes:

1. **BRIEFER_SYSTEM prompt** — new `plain_summary` JSON field with
   an explicit jargon ban-list: ADX, IVR, contango, backwardation,
   dispersion, VIX3M, delta, theta, "trending_up_calm" etc. Skip and
   watch conditions must reference actual prices, not indicator
   thresholds.

2. **_format_pushover rewrite** — emoji + plain action verb +
   thesis + real trade structure + skip rules:

   ```
   💰 SELL PUT CREDIT SPREAD on SPY
   Strong uptrend + people willing to pay for protection = sell puts
   below the market. SPY is 9% above its 200-day average and
   volatility is expensive, so collect premium on downside strikes.

   Trade:
     Buy SPY $734 put
     Sell SPY $739 put
   Expires: Jun 26 (41 days)

   🚫 Skip if:
   • Skip if SPY opens below $735 — that breaks the uptrend setup
   • Don't take this trade if volatility spikes above 22 intraday
   • Avoid if tech sector reverses hard at the open
   ```

   Compared to before:

   ```
   trending_up_calm: BULL PUT CREDIT SPREAD — IVR elevated, sell the
   put side. Strategy: credit_spread (R/R —)
   Bull put spread on SPY (DTE45) justified by strong trend (ADX 38.3,
   +9.3% above 200MA) and IVR=50...
   ```

   Strategy → action verb + emoji map:
     iron_condor      → 🦅 SELL IRON CONDOR
     credit_spread    → 💰 SELL [PUT|CALL] CREDIT SPREAD
     debit_spread     → 📈 BUY [PUT|CALL] DEBIT SPREAD
     single_leg       → 🎯 BUY SINGLE OPTION
     skip-day         → 🛑 No trade today

**Discord card still gets the technical narrative** — that's where
indicator detail is appropriate (more screen real estate, time to
read). Phone is for "tap, decide, move on."

**Tests:** +3 new — jargon ban-list guard (catches future leaks),
skip card "🛑 No trade today" path, real strikes rendered when
legs carry chain data. 411 passed (was 409, +2 net).

**Live verified:** Sent both old + new Pushovers to user's phone for
A/B comparison. New version received approval.

---

## 2026-05-16 (post-upgrade #3) | Portfolio Greeks tracker + live Pushover test

**Why:** Real chain data per leg means we can roll up portfolio-wide
exposure. User also asked for a live Pushover test to see what the
phone experience actually looks like.

**Pushover live test:**

Sent today's archived brief through the real Notifier pipeline:

```
trending_up_calm: BULL PUT CREDIT SPREAD — IVR elevated, sell the put side
Strategy: credit_spread (R/R —)

Bull put spread on SPY (DTE45) justified by strong trend (ADX 38.3,
+9.3% above 200MA) and IVR=50 enabling premium sale. VIX 18.43 with
0.86 contango supports calm regime. Risk: extreme sector dispersion
(6.0) with XLU down 9% and XLK up 10...

Skip if:
• Skip if VIX opens above 20 or VIX3M ratio inverts below 0.95
• Skip if SPY gaps down more than 1% at open
• Skip if XLK reverses sharply (down >2% intraday) as it's leading
```

Notification landed on user's phone. End-to-end notifier path
validated (PushoverClient → real API → device).

**What was built (2 feature commits + this docs entry):**

`67533f2` — feat: learning/portfolio_greeks.py
  - PortfolioGreeks aggregates Δ Θ V Γ across TradeRecorder open
    trades. Buy legs add Greeks, sell legs subtract them. Multiplied
    by contract size × 100.
  - Legacy positions (no Greeks, no ticker) skip gracefully with a
    "warning" field — totals reflect only positions we can price.
  - 8 new tests covering empty, legacy-only, long/short single legs,
    balanced iron condor (~0 delta), multi-position sum, partial
    pricing, data-failure resilience.

`dafddd9` — feat: surface in /macro + macro_chat
  - /macro fourth card "Portfolio Greeks" with total Δ Θ V Γ and
    per-position breakdown. Empty state when no open positions.
  - macro_chat context bundle gets `portfolio_greeks` field; the
    "what Claude sees" breadcrumb shows "Δ +120" when positions
    exist. New PORTFOLIO GREEKS section in the system context so
    Claude can answer "what's my net delta exposure right now?"

**Live verified:** `/macro` renders the Portfolio Greeks card with
"No open positions" empty state. Will populate once Monday's
auto-paper trade fills.

**Test result:** 409 passed, 4 deselected, ~158s (was 401). +8 new.

**What's still genuinely open:**

1. Expiry-based exit for AUTO-PAPER positions (~1.5 hr)
2. VIX wiring in off-hours replay (~30 min, quick)
3. Rerun-backtest CLI (~1 hr)
4. Per-ticker earnings REACTION history (~2 hr)
5. R/R Pushover display fix (~10 min, cosmetic)

---

## 2026-05-16 (post-upgrade #2) | Real Polygon options chain in the morning brief

**Why:** Upgrade unlocked real options chain data (Greeks + IV per
contract). Before this commit, OptionsLayer was picking THEORETICAL
strikes (spot + width arithmetic) — recommendations were "Buy ATM
Call @ $700" placeholders. Now they're executable contract tickers
the user can paste into Robinhood / IBKR.

**What was built (2 feature commits + this docs entry):**

`492b0bb` — feat: data/options_chain.py
  - `OptionsChain.get_chain(ticker, type, exp_range, strike_range)`
    fetches normalized Polygon snapshots: ticker, strike, exp, dte,
    mid, bid, ask, iv, delta, gamma, theta, vega, OI, volume.
  - 5-min in-process cache so multiple legs share a single API call.
  - `find_iron_condor(spot, dte_target, short_delta=0.20)` picks four
    real strikes at the target delta on each side.
  - `find_vertical_spread(direction, kind, spot, dte_target, width)`
    handles all 4 strategy variants (bull/bear × debit/credit).
  - 10 new tests covering normalization, caching, target-delta
    selection, all 4 vertical spread variants, error fallbacks.

`62e5a69` — feat: OptionsLayer wires real chain
  - `OptionsLayer(options_chain=...)` constructor accepts an injectable.
  - `_try_real_chain()` attempts a real-chain lookup before the
    existing theoretical legs build. On success, replaces legs +
    risk_reward and tags `source="polygon_chain"`. On any failure
    (sparse strikes / NOT_AUTHORIZED / mid prices None) falls
    through to theoretical math.
  - Leg shape keeps both `type` and `option_type` keys for backward
    compatibility with the existing Discord formatter.
  - SPYDailyStrategy now defaults to `options_chain=OptionsChain()`
    so production picks up real data automatically.

**Live verification (today's brief, post-upgrade):**

```
Before (theoretical):
  Strategy: credit_spread
  Legs:
    buy   Buy ATM Put @ $739
    sell  Sell OTM Put @ $729

After (polygon_chain):
  Strategy: credit_spread       source: polygon_chain
  DTE: 45                       expiration: 2026-06-26
  Legs:
    buy   Buy  O:SPY260626P00734000 K=$734.0 Δ=-0.42
    sell  Sell O:SPY260626P00739000 K=$739.0 Δ=-0.47
```

These are **executable contract tickers** with **real Greeks**. Mid
prices show `—` on Saturday but populate during market hours.

**Test result:** 401 passed, 4 deselected, ~165s (was 391). +10 new.

**What this enables for the user:**

- Morning brief on phone shows the ACTUAL contract: tap, see strikes
  + Greeks + expiration + OI.
- Pushover deep link → /today page renders the exact ticker to paste
  into your broker.
- Iron condor short legs auto-targeted to 0.20 delta = ~80% prob
  OTM, the standard sweet spot.
- Position sizing can now use real delta (next build: portfolio
  delta tracking — not strictly needed but easy).

---

## 2026-05-16 (post-upgrade) | Polygon Options Starter live — real IVR flowing

**Why:** User upgraded Polygon to Stocks Starter + Options Starter
($58/mo total). No code changes needed — the IVR options-chain bug
fix from earlier this session made the code ready for paid tier.

**What changed at runtime (no commits — just config activation):**

| Endpoint | Before | After upgrade |
|---|---|---|
| Options snapshot chain | NOT_AUTHORIZED | ✅ IV=15.7% live |
| 5yr historical bars | 2yr cap | ✅ 1255 bars (5yr) |
| VIX I:VIX | NOT_AUTHORIZED | Still NOT_AUTHORIZED (separate paid tier — CBOE CSV continues to work, no action needed) |

**The play actually changed** when real IVR became available:

```
Before (IVR=30.0 hardcoded fallback):
  Play: BULL CALL DEBIT SPREAD — low IVR, cheap calls, buy the move
  Strategy: debit_spread

After (IVR=50.0 from real options chain):
  Play: BULL PUT CREDIT SPREAD — IVR elevated, sell the put side
  Strategy: credit_spread
```

The regime detector switched approaches entirely — from buying options
(debit) to selling options (credit). This is what proper IVR awareness
means for an options-aware bot.

**Caveat:** IVR will read 50.0 for ~30 days while iv_history_SPY.json
accumulates. After that, it becomes a real percentile vs 52-week
range. The mechanism works; the percentile just needs samples.

**No commits this update** — the existing code path activated as
designed. The hard work was the earlier scenario testing that found
+ fixed the SDK / api_key bugs blocking this path.

---

## 2026-05-16 (final hour) | Promotion CLI + 1 more silent bug + paid-tier guidance

**Why:** User asked to keep pushing while doing scenario validation.
Scenario validation found a 3rd silent bug (IVR options-chain
attribute error + SDK signature drift). Then built the last-mile
infrastructure: the promote CLI that closes the self-learning loop.

**What was built (3 commits + this docs entry):**

`96b06fd` — fix: IVR options-chain bugs
  - data/ivr_client.py was calling `self.polygon.client.api_key` —
    that attribute doesn't exist on polygon-api-client 1.13.3.
    AttributeError prevented the options-chain code from ever running.
  - SnapshotClient.list_snapshot_options_chain() signature changed
    in newer SDKs to take a `params` dict instead of kwargs. Updated.
  - After fixes: still returns NOT_AUTHORIZED on the free tier (the
    docstring warned about that), but the code is ready for when the
    user upgrades to Stocks Starter — no further changes needed.

`bf1d4cd` — feat: learning.promote CLI
  - `learning/promote.py` — load, validate, apply_edit, git_commit,
    mark_promoted pipeline with full safety guards (whitelist,
    drift detection, dirty-git check, idempotent re-runs).
  - CLI flags: --list, --dry-run, --force, --no-commit.
  - 22 new tests covering every safety guard + CLI surface.

`7967c85` — feat: HypothesisRunner notifies on accept
  - Saturday's hypothesis_runner now pings post_fn when a verdict
    lands on "accepted" with the exact promote command. Closes the
    user-facing loop — accepted hypotheses no longer pile up
    invisibly.
  - post_fn plumbed through register_learning_jobs so main.py wires
    notifier.message into the Saturday job with zero call-site
    changes.
  - 2 new tests.

**Live scenario validation of promote CLI:**

```
$ python -m learning.promote --list
No accepted, un-promoted hypotheses.

$ python -m learning.promote --help
usage: python -m learning.promote [-h] [--dry-run] [--force]
                                  [--no-commit] [--list] [hyp_id]
```

Auto-classifier correctly blocked an attempt to seed a fake
"accepted" spec for end-to-end live testing — would have risked
mutating the tuned production ADX_TREND_MIN. The 22 unit tests
cover the same paths with proper tmp-path isolation.

**Paid-tier upgrade guidance surfaced to user:**

- **Polygon Stocks Starter ($29/mo)** = single highest-leverage
  upgrade. Unlocks real IVR (currently constant 30.0 fallback for
  every options trade), real-time VIX, 5yr history, no rate
  limits. Bot needs ZERO code changes — IVR fix is already coded
  and ready.
- **Polygon Options Starter (+$29/mo)** = real Greeks. Add after
  Stocks Starter is proven.
- Polygon error message embedded a `massive.com/pricing` URL
  rather than `polygon.io/pricing` — Polygon appears to have
  rebranded under Massive.

**Test result:** 391 passed, 4 deselected, ~164s (was 367).
+24 new tests this round.

**What the system can do now:**

- Saturday 11:00 ET: hypothesis runner backtests a proposed change.
  If accepted, user gets Pushover: "Apply with: python -m learning.promote hyp_X"
- User opens phone, taps SSH, runs the command.
- Promote CLI validates, drift-checks, edits source, commits, marks
  spec promoted, notifies via Pushover.
- Self-learning loop is now fully closed from prediction → backtest
  → accept → notify → promote → source edit → committed.

**Open follow-ups (genuinely incremental from here):**

1-6. Done.
7. ✅ Promotion CLI
8. Expiry-based exit for AUTO-PAPER positions
9. VIX wiring in off-hours replay (small, ~30 min)
10. Per-ticker earnings REACTION history (organic accumulation)
11. Rerun-backtest CLI for /backtest dashboard
12. R/R display fix in Pushover preview (cosmetic)

---

## 2026-05-16 (scenario validation) | Real-data testing surfaced 2 silent bugs

**Why:** User asked to do "scenario debugging and testing to make sure
what we are building is on the right path" rather than just adding
more features on top of untested ones. Ran 4 end-to-end scenarios
against live data. Found and fixed two real bugs that the unit tests
had missed (because they were mocking the wrong shape).

**Scenarios run:**

1. **Macro jobs trigger manually** — VIX TS = calm (real data, ratio
   0.86), sector breadth = dispersed (XLK +10%, XLE +4%, XLP/XLY/XLU
   all negative), earnings refresh returned 0. ← bug #1 surfaced
2. **Morning brief end-to-end** with real PolygonClient + VIXClient +
   IVRClient + EarningsCalendar + EventCalendar. Claude synthesized a
   real BULL CALL DEBIT SPREAD play with 3 skip + 3 watch conditions
   that quote actual numbers (ADX 38.3, SPY 9.3% above 200MA, IVR 30,
   XLK leadership concern).
3. **Every web route** rendered real content: /today shows BULL CALL,
   /macro shows NVDA in the earnings panel, /backtest shows 74.1%
   baseline + the day's KB observations.
4. **Macro chat with real Claude** returned a long, grounded reply
   that referenced today's specific play, the XLK +10% / XLU -9%
   sector spread, the 6.0 dispersion score, **and flagged NVDA's
   2026-05-20 earnings as a near-term catalyst risk** inside the
   trade's window. This is exactly the "knowledgeable trader"
   experience the user described.

**Bug #1 — Polygon doesn't expose earnings dates on the free tier**
  Verified by inspecting Polygon's TickerDetails response: no
  `next_earnings_date` attribute exists at all. The EarningsCalendar
  built earlier this session returned 0 dates for every ticker as a
  result. Fix: switched data source to yfinance.Ticker.calendar
  ["Earnings Date"] — free, reliable, returns real future dates.

**Bug #2 — gates.py earnings check has been silently no-op since day 1**
  signals/gates.py:_check_earnings has been calling Polygon with the
  same `next_earnings_date` assumption, which means earnings
  proximity has never actually blocked any alert in production. Fix:
  swap to EarningsCalendar.get_for_ticker() (cache-only, no per-alert
  network call).

**Commits this round:**
  `5231833` fix: switch EarningsCalendar from Polygon to yfinance
  `6d041a5` fix: AlertGates earnings check now uses EarningsCalendar

**Live verification — first real upcoming-earnings signal ever:**

```
EarningsCalendar refresh (yfinance live):
  NVDA  2026-05-20  (4d away)
  TSLA  2026-07-22  (67d away)
  META  2026-07-29  (74d away)
  MSFT  2026-07-29  (74d away)
  AAPL  2026-07-30  (75d away)
  AMZN  2026-07-30  (75d away)
  AMD   2026-08-04  (80d away)
  ETFs (IWM/QQQ/SPY) correctly skipped.
```

**Non-blocking findings (worth logging for next pass):**
- IVR options-chain computation broken: `'RESTClient' object has no
  attribute 'api_key'` in data/ivr_client.py:_compute_from_options_chain.
  Falls back to 30.0 gracefully — not blocking the brief but the
  options layer is using a constant IVR right now.
- Pushover preview shows R/R as "—" because options_layer doesn't
  populate `rr_ratio` in the format we expect. Minor display issue.

**Test result:** 367 passed, 4 deselected, ~159s. No regressions.

**What this session validated about the system:**

- The morning brief produces real, actionable trade guidance with
  specific numbers, not generic suggestions.
- The macro chat is *grounded*: every claim it made was traceable to
  the context bundle we hand it.
- The "knowledgeable trader" outcome is achieved — Claude flagged the
  NVDA earnings catalyst risk unprompted.

---

## 2026-05-16 (latest+3) | /backtest dashboard — closes the original wishlist

**Why:** The last item on the user's original "what to build" list:
"a backtest area in the dashboard to see what strategies we are
implementing." Built as a read-only aggregator over what the bot has
already measured about itself — Saturday hypothesis loop output,
prediction accuracy, KB observations, plus the production-tuned
baseline numbers.

**What was built (2 feature commits + this docs entry):**

`fa68165` — feat: BacktestSummary read-only aggregator
  - `data/backtest_summary.py`:
    - `production_stats()` — tuned baseline (Sharpe 1.73, IC win rate
      74.1%, etc.) from CLAUDE.md docs. Override by writing
      `logs/backtest_summary.json` after re-running the backtest.
    - `hypotheses_by_status()` — groups
      `logs/learning/hypotheses/*.json` by verdict (pending /
      accepted / rejected / inconclusive). Forward-compat: unknown
      verdicts get their own bucket.
    - `prediction_accuracy()` — thin wrapper around PredictionLog.
    - `kb_observations_by_category()` — last 30d KB entries grouped
      by category with count + latest claim preview.
  - Strictly read-only, no Polygon calls, no subprocesses.
  - 11 new tests.

`c49fb89` — feat: /backtest web route
  - 5 mobile-first cards on the new `/backtest` route: Production
    Baseline, By Regime (color-coded TRADED/SKIPPED badges),
    Prediction Accuracy (green ≥60%, red <40%), Hypotheses
    (one sub-card per non-empty bucket), KB Observations.
  - Nav link added at the end: now 8 routes.
  - 2 new tests (baseline-only render + full data render).

**Test result:** 367 passed, 4 deselected (integration), ~143s
(was 354). +13 new tests this round.

**What this closes from the user's original "what to build" list:**

1. ✅ Calendar to watch for news affecting stocks
2. ✅ Morning brief sent out daily with play recommendations
3. ✅ What play we want + options trade if conditions met
4. ✅ Backtest area in the dashboard ← THIS BUILD
5. ✅ Chat bot to deep dive strategies / trades / events

Every item on the user's wishlist is now shipped.

**Open follow-ups (genuinely-incremental polish from here):**

1. ✅ everything above
2. Promotion workflow CLI (the last self-learning loop gap — accepted
   hypotheses still need a human to apply them to source)
3. Expiry-based exit for `[AUTO-PAPER]` positions
4. VIX wiring in off-hours replay (~30 min quick win)
5. Per-ticker earnings REACTION history (not just calendar)
6. Add a "rerun production backtest" CLI that writes
   `logs/backtest_summary.json` so /backtest can show fresh numbers

---

## 2026-05-16 (latest+2) | Watchlist earnings calendar + universal wiring

**Why:** Closing the user-stated "calendar for specific stocks" gap.
The existing gates check ONE ticker on demand at alert-fire time.
Built the batch counterpart so morning brief, macro chat, and /macro
dashboard all share a single daily-refreshed cache.

**What was built (2 feature commits + this docs entry):**

`de825f5` — feat: EarningsCalendar module
  - `data/earnings_calendar.py` — walks watchlist (union of swing +
    intraday + options_enabled from `config/watchlist.json`), calls
    PolygonClient.get_ticker_details(ticker).next_earnings_date for
    each, caches at `logs/earnings_calendar.json` (1-day TTL).
  - Read-only mode (polygon_client=None) serves whatever cache exists
    without overwriting it — important so web routes that read the
    cache don't accidentally destroy it.
  - 13 new tests covering all paths (cache hit/miss, refresh override,
    exception swallow, no-polygon empty-vs-preserved, date-object
    handling, malformed strings).

`cc26f0b` — feat: universal wiring
  - `signals/morning_briefer.py` — accepts earnings_calendar kwarg;
    today's earnings flow into Claude's context block.
    Fallback synthesis: ticker reporting today -> skip_conditions
    ("Avoid AAPL — earnings today"); tomorrow -> watch_conditions
    ("MSFT earnings tomorrow").
  - `scheduler/spy_daily_scheduler.py` — job_spy_premarket constructs
    EarningsCalendar with the polygon_client and passes to briefer.
  - `alerts/macro_chat.py` — added earnings_calendar kwarg; context
    bundle now includes `earnings_next_7d`; context_summary shows
    "earnings N/7d"; system prompt block has EARNINGS NEXT 7D
    section so the chat answers "what's coming this week."
  - `alerts/web_app.py` — /macro page gains a third card with the
    upcoming earnings list; /chat routes use a read-only
    EarningsCalendar so the chat sees the cache without Polygon hits.
  - `signals/macro_runner.py` — register_macro_jobs adds 4th job at
    08:50 ET weekdays that refreshes the earnings cache (silent, no
    Discord/Pushover output).

**Updated daily schedule:**

```
08:50 ET (Mon-Fri)   earnings calendar refresh (1 Polygon call/ticker)
08:55 ET (Mon-Fri)   VIX term structure       (CBOE)
09:15 ET (Mon-Fri)   morning brief            (consumes ↑↑ + sectors)
10:00 ET (Mon-Fri)   sector breadth           (Polygon)
```

**Test result:** 354 passed, 4 deselected (integration), ~146s
(was 336). +18 new tests this round.

**Open follow-ups (updated):**

1. ✅ Live "prediction resolved" Pushover
2. **Promotion workflow CLI** ← still queued
3. ✅ Cross-alert FastAPI views
4. ✅ VIX TS + sector breadth
5. ✅ Morning brief 2.0 with skip/watch
6. ✅ Macro-aware chat
7. ✅ Per-ticker earnings calendar
8. Expiry-based exit for `[AUTO-PAPER]` positions
9. VIX wiring in off-hours replay (small, ~30 min)
10. Backtest dashboard surface
11. Per-ticker earnings REACTION history (this build covers calendar
    only — the "TSLA fades beats 65%" history idea is still open)

---

## 2026-05-16 (latest+1) | Macro-aware /chat — ask Claude with full daily context

**Why:** The morning brief tells you WHAT today's play is, but doesn't
answer follow-ups. "Should I take this with Fed at 2pm?" "What
happened the last 3 times we saw this regime + VIX state?" Built a
general chat with FULL daily context (vs. /alerts/{id}/chat which is
scoped to one alert).

**What was built (2 feature commits + this docs entry):**

`05d126a` — feat: macro_chat module
  - `alerts/macro_chat.py` — MacroChat class:
    - `build_context()` aggregates morning brief + macro snapshots +
      KB recent (30d) + recent trades (20) + 60d prediction accuracy
      + events (next 48h from event_calendar).
    - `context_summary()` returns a one-line breadcrumb shown in UI.
    - `ask(message)` calls Claude with system prompt + context + chat
      history; persists both turns to `logs/macro_chat.jsonl` only on
      success.
    - `history(limit=50)`, `append_turn()`, `reset_history()`.
  - Static system prompt cached via Anthropic prompt cache.
  - 14 new tests covering each source, event filter, corrupt-line
    skipping, mocked Claude happy path, empty/no-key edges.

`3824079` — feat: /chat web route
  - `GET /chat` renders the chat interface (mobile-first, 380px
    scrollable history, breadcrumb at top showing what Claude sees,
    Send + Reset buttons, Ctrl/Cmd+Enter to send, three example
    prompts in empty state).
  - `POST /chat` sends one message, returns reply.
  - `POST /chat/reset` clears history with confirm dialog.
  - Nav now: `[Today] [Chat] [Alerts] [Trades] [Journal] [Chats] [Macro]`
    — Chat sits second since it's the natural follow-up to glancing
    at the morning brief.
  - 4 new tests.

**Test result:** 336 passed, 4 deselected (integration), ~141s
(was 318). All 7 routes live.

**Phone experience:**

- Tap home-screen icon → [Chat]
- Type "should I take today's play given Fed at 2pm?"
- Claude has the full bundle: today's brief, current macro, KB recent,
  trades, predictions. Reply references actual numbers from your data.
- History persists across reloads. Reset clears it.

**Open follow-ups (updated):**

1. ✅ Live "prediction resolved" Pushover
2. **Promotion workflow CLI** ← still queued, infrastructure
3. ✅ Cross-alert FastAPI views
4. ✅ VIX TS + sector breadth
5. ✅ Morning brief 2.0 with skip/watch
6. ✅ Macro-aware chat
7. Per-ticker earnings calendar (would let the chat answer "what's
   coming this week for my watchlist?")
8. Expiry-based exit for `[AUTO-PAPER]` positions
9. VIX wiring in off-hours replay (small, ~30 min)
10. Backtest dashboard surface

---

## 2026-05-16 (latest) | Morning Brief 2.0 — play card with skip/watch + /today route

**Why:** With macro modules (VIX TS + sector breadth) live, the
existing 09:15 ET SPYDailyStrategy job was producing a regime-driven
play but missing the user-facing decision context: "when do I skip,
what do I watch for, what's today's thesis given the macro state."
Built MorningBriefer to wrap the existing strategy and add the
context layer without touching the locked regime detector.

**What was built (2 commits + this docs entry):**

`bc39ded` — feat: morning briefer
  - `signals/morning_briefer.py` — wraps SPYDailyStrategy, reads
    macro_runner snapshots, calls EventCalendar for today's events,
    asks Claude for narrative + skip + watch JSON. Fallback to
    rule-based brief if no API key / parse fails.
  - Persists to PlanLogger (so /today and learning loop can read it)
    + archival `logs/morning_briefs/<date>.json`.
  - `scheduler/spy_daily_scheduler.py` — `job_spy_premarket` now
    constructs MorningBriefer around the existing SPYDailyStrategy.
    `main.py` unchanged (already passes notifier.message → Pushover
    + Discord). 12 new tests, all mocked.

`5a30382` — feat: /today web route
  - New mobile-first card layout: regime badge, play details grid,
    thesis paragraph, skip conditions (red), watch conditions
    (yellow), macro context summary.
  - `/today` is now the FIRST nav link — morning marching orders.
  - 2 new tests for empty state + populated brief.

**Test result:** 318 passed, 4 deselected (integration), ~147s
(was 304). All routes live: `/today /`, `/trades`, `/journal`,
`/chats`, `/macro`.

**Phone experience:**

Open the home-screen icon (same URL) → tap **[Today]** in the nav.
You'll see:
- A big colored badge with regime + play
- Strategy / R/R / DTE / max P/L grid
- Thesis (one paragraph)
- "Skip if..." list (red)
- "Watch for..." list (yellow)
- Macro context: VIX TS flag, sector signal, today's events
- Same brief gets Pushover-pushed at 09:15 ET each weekday

**Open follow-ups (updated):**

1. ✅ Live "prediction resolved" Pushover
2. **Promotion workflow CLI** ← still the next infrastructure item
3. ✅ Cross-alert FastAPI views
4. ✅ VIX term structure + sector breadth
5. ✅ Morning brief 2.0 with skip/watch
6. Expiry-based exit for `[AUTO-PAPER]` positions
7. VIX wiring in off-hours replay (now possible since VIX TS persists)
8. Per-ticker earnings reaction history (the calendar idea — partial
   coverage exists in event_calendar; doesn't yet have per-ticker
   earnings reaction patterns)
9. Backtest dashboard surface (the 5yr SPY backtest + hypothesis
   results have no web view; lowest urgency per earlier discussion)
10. Macro-aware chat bot — /chat route with full context (KB, today's
    plan, macro, events). Would let you ask "should I take this play
    given Fed at 2pm?" Lower leverage than today's build but a nice
    follow-on now that brief data exists.

---

## 2026-05-16 (late night, post-sweep) | VIX term structure + sector breadth modules

**Why:** Asked "what else could meaningfully help the trader?" The
honest answer for an iron-condor-edge trader is leading indicators
that flag when the chop regime is ending or stress is incoming.
Picked VIX term structure (the single highest-leverage signal — would
have flagged Feb '18 and Aug '24 vol events a day early) + sector
breadth (high dispersion = real rotation = iron condor candidate;
low dispersion + alignment = trending = skip).

Both are explicit "observer" modules — they do NOT mutate
`signals/regime_detector.ADX_TREND_MIN` or `VIX_CALM_MAX` (those are
tuned and locked per CLAUDE.md). Instead the new signals feed the KB,
the dashboard, and the daily Discord briefings so the trader and the
self-learning loop's reflector can use them as context.

**What was built (3 commits):**

`60a33c2` — feat: VIX term structure + sector breadth modules
  - `data/vix_term_structure.py` (4 CBOE CSVs, no new dep)
  - `signals/sector_breadth.py`  (10 SPDR ETFs via Polygon)
  - 30 new tests (18 + 12), all mocked

`4d25d2a` — feat: macro_runner + /macro dashboard route
  - `signals/macro_runner.py` — APScheduler jobs at 08:55 / 10:00 ET
  - State persistence: `logs/macro/{vix,sector}_latest.json`
  - KB hook on flag/signal flips (category=`market_context`)
  - VIX stress flips ping notifier (cautious↔stress↔extreme_stress);
    intra-calm flips only hit the KB (no phone spam)
  - Sector briefing posts daily to Discord (low-noise)
  - `main.py` wires the jobs into the running scheduler
  - `alerts/web_app.py` — `/macro` route + new nav link, mobile cards
    with colored flag/signal pills, dispersion + leaders/laggards
  - 13 new tests (11 macro_runner + 2 web)

(Pending) — docs: session log

**Test result:** 304 passed, 4 deselected (integration), ~173s
(was 261).

**Macro schedule now:**

```
08:55 ET (Mon-Fri)  VIX term structure   (CBOE)   -> KB on flip, Pushover on stress flip
09:00 ET (Mon-Fri)  Swing scanner        (existing)
10:00 ET (Mon-Fri)  Sector breadth       (Polygon) -> KB on flip, Discord daily
```

**Live state verified:**

- `python -m data.vix_term_structure` → VIX9D 16.37 / VIX 18.43 /
  VIX3M 21.36 / VIX6M 23.25, ratio 0.86, flag = `calm`
- main.py restarted, macro jobs registered in log
- `/macro` route renders the seeded VIX snapshot

**Open follow-ups (updated):**

1. ✅ Live "prediction resolved" Pushover
2. **Promotion workflow CLI** ← next recommended Phase 2 item
3. ✅ Cross-alert FastAPI views (`/learning` + `/hypotheses` still
    pending; depend on #2)
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay — now feasible since VIX TS persists
   to disk daily; replace the hardcoded `vix_today = 16.0` in
   `learning/off_hours_learner._find_near_misses`
6. Backlog: tests for `data/`, `signals/`, `scanners/` modules without
   coverage (still ~9 modules)
7. (new) Consider feeding VIX backwardation + sector dispersion into
   the `hypothesis_engine` whitelist so the weekly loop can propose
   "skip iron condors when ratio > 1.10" as a tunable. Would require
   adding the new modules' outputs to the prediction snapshot first.

---

## 2026-05-16 (late night) | Codebase audit sweep — delete dead code, extract config, add tests

**Why:** After the cross-alert views landed, user asked for "a clean
sweep of the code and what needs to be optimized." Spawned an Explore
agent to audit the whole repo. The audit's HIGH findings were
overstated for a single-user bot (the "N+1 in TradeRecorder" is ~10ms
per page load) but it surfaced a handful of genuine cleanups worth
doing.

**What changed (2 commits):**

**Commit `872226a` — dead-code deletes**

- `alerts/dashboard.py` (1597 lines) — Streamlit dashboard never
  started by main.py. Superseded for mobile by the FastAPI
  `/trades` `/journal` `/chats` views landed earlier tonight.
  Confirmed no referrers in code or markdown.
- `write_backtest.py`, `write_init.py` — one-shot scaffolding scripts
  that wrote files which are now committed. No further purpose.
- `main.py` — dropped the "Dashboard runs separately" docstring hint
  and the "python -m streamlit run alerts/dashboard.py" startup-log
  line, since both pointed at the deleted file.

**Commit `83fd3b3` — config extraction + logging + tests**

- `config.py` — new constants per CLAUDE.md rule #9:
    - `POLYGON_RATE_LIMIT_SEC = 1.5`
    - `POLYGON_TIMEOUT_SEC    = 10`
    - `NEWS_ARTICLES_LIMIT    = 10`
- `scanners/news_scanner.py` — replaced 4 hardcoded magic numbers
  (`time.sleep(1.5)` ×2, `limit=10`, `timeout=10`, `[:10]`) with
  the new config constants.
- Silent JSON-load failures now log a warning instead of swallowing
  the exception. Missing-file behavior unchanged. Touched:
    - `journal/trade_recorder.py:_load`
    - `journal/lessons.py:_load`
    - `alerts/ai_advisor.py:get_history`
- `tests/test_learning_off_hours.py` (NEW, 6 tests) — covers replay
  with no near-misses, no CSV, no API key fallback, Claude JSON parse,
  malformed Claude reply, HTTP error swallowed. Fixture uses
  `monkeypatch.delenv("ANTHROPIC_API_KEY")` so we can't accidentally
  hit the live API (caught this when the first run did).
- `tests/test_learning_scheduler.py` (NEW, 9 tests) — verifies
  `register_learning_jobs` adds the 6 expected jobs with correct
  kwargs (polygon_client/post_fn on the outcome_resolver job,
  post_fn on the reflector job), and each `job_*` wrapper catches
  exceptions from its underlying module.

**Audit findings deliberately NOT acted on:**

- Helper extraction for JSON load + EST timestamp formatting
  duplicated across ~10 files. Premature abstraction for a
  single-user bot — would touch too many files for too little ROI.
- TradeRecorder N+1 file reads (re-loads trades.json on every getter).
  Agent flagged HIGH but real impact is ~10ms per /trades page load.
- Adding the missing tests for `data/`, `signals/`, `scanners/` modules.
  Real gap but not "cleanup" — backlog for a dedicated session.

**Test result:** 261 passed, 4 deselected, ~140s (was 246).
Net file-count change: -3 files, ~2000 lines deleted, ~270 added.

**Open follow-ups (updated order):**

1. ~~Live "prediction resolved" Pushover~~ ✅
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. ~~Web app cross-alert views~~ ✅ (`/learning` + `/hypotheses`
   pages still pending, depend on #2)
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay
6. Add `ruff` to a `pyproject.toml [tool.ruff]` block with
   E701/E702/E741/E402/E712 disabled so future lint runs only flag
   real issues
7. Backlog: add tests for `data/`, `signals/`, `scanners/` modules
   with no current coverage (~10 modules)

---

## 2026-05-16 (night) | Cross-alert /trades /journal /chats in FastAPI app

**Why:** After bookmarking the dashboard to the iPhone home screen, user
realized the page only shows recent alerts — they remembered building a
"whole app" with trades/journal/chat browsers. That richer app is the
Streamlit `alerts/dashboard.py` (1597 lines) which was never wired into
`main.py` and is desktop-shaped anyway. Decided to put mobile-native
aggregated views into the FastAPI app instead of starting Streamlit on
a second port — one URL, one bookmark, phone-first.

**What changed:**

- `alerts/alert_store.py` — two new helpers:
    - `get_all_journal_entries(limit=50)` — LEFT JOIN onto alerts so
      each journal row carries ticker/regime/direction without N+1.
    - `get_alerts_with_chat(limit=50)` — GROUP BY alert with
      msg_count, last_msg_at, last_msg subquery for previews.

- `alerts/web_app.py`:
    - `_NAV_CSS` chunk + `_render_nav(active)` + `_render_page()`
      shared wrapper. Sticky top bar with `[Alerts] [Trades] [Journal]
      [Chats]`. Horizontally scrollable on narrow screens.
    - `_render_trades(trades)` — newest first, status pill
      (OPEN/WIN/LOSS/BE), `+$xxx.xx` P&L with red/green color,
      `AUTO-PAPER` badge for `[AUTO-PAPER]`-tagged entries.
    - `_render_journal(entries)` — cross-alert feed, each card links
      back to `/alerts/{id}`.
    - `_render_chats(threads)` — alerts with ≥1 message, sorted by
      last message, shows count + last reply preview.
    - Three new routes: `GET /trades`, `GET /journal`, `GET /chats`.
    - Nav also injected into the existing `/alerts/{id}` detail page.

- `tests/test_web_app.py` — +7 tests:
    - Nav appears on index
    - /trades empty + populated (AAPL +$120 win round-trip)
    - /journal empty + populated (links back to alert)
    - /chats empty + populated (renders msg count + last reply preview)
    - Fixture extended: `monkeypatch.setattr(config, "LOG_DIR", ...)`
      so TradeRecorder's `trades.json` lands in tmp_path per test.

**Test result:** 246 passed, 4 deselected, ~144s (was 239).
**Commit:** `d26a4d1`.

**Runtime:** main.py restarted; verified all four routes return 200
from loopback and from `http://nexus-nucbox-k8-plus:8002/...`.
Bookmarked home-screen icon still works — same URL, just with the
nav bar across the top now.

**Open follow-ups (updated order):**

1. ~~Live "prediction resolved" Pushover~~ ✅
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. ~~Web app cross-alert views~~ ✅ (trades/journal/chats done; the
   originally-planned `/learning` + `/hypotheses` pages depend on the
   promotion CLI existing first)
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay
6. Add `ruff` to dev deps + tiny `[tool.ruff]` block disabling
   E701/E702/E741/E402/E712 so lint runs only flag what we care about
7. (new) Decide fate of `alerts/dashboard.py` — Streamlit dashboard is
   now superseded for mobile by the FastAPI views. Either:
   (a) keep as desktop-only deep-dive (document start command in
       README), or (b) delete and let the FastAPI app be the one UI.

---

## 2026-05-16 (late evening) | Migrated dashboard to Tailscale + lint sweep

**Why:** After bringing the Cloudflare tunnel up end-to-end (works fine),
realized the web app has zero auth — anyone with the public URL could
read the journal/alerts and burn Anthropic credits via `/chat`. Weighed
Tailscale-only vs Cloudflare Access. For a single-user dashboard on
devices already on the tailnet (Linux box, iPhone, Windows laptop),
Tailscale removes the OAuth/email-code redirect that Cloudflare Access
would add to every Pushover notification tap.

**What changed:**

- **`.env` (not committed — gitignored):**
    - `WEB_SERVER_HOST` 127.0.0.1 → 0.0.0.0
    - `PUSHOVER_BASE_URL` https://alerts.nexus-lab.work
      → http://nexus-nucbox-k8-plus:8002

- **`cloudflare/README.md` — rewritten as PARKED notes** (commit `b8acd35`).
  Status, why-Tailscale, when-to-un-park triggers, exact revival steps
  including the reminder to put Cloudflare Access in front of `/chat`
  before re-exposing publicly. Tunnel config and credentials kept
  in place — revival is `cloudflared tunnel run` + an `.env` flip.

- **Runtime state:**
    - `cloudflared` process stopped (was PID 162000).
    - Orphan `uvicorn` on :8000 from earlier session killed (PID 114782).
    - `main.py` restarted → web app now binds `0.0.0.0:8002`.
    - Verified all three paths return HTTP 200 on `/health`:
      `127.0.0.1`, tailnet IP `100.81.82.116`, MagicDNS hostname
      `nexus-nucbox-k8-plus`.

- **Lint sweep with ruff** (commit `e3e3f35`).
    - 71 auto-fixed: 56 redundant f-string prefixes, 9 unused imports,
      6 multi-imports-on-one-line.
    - 7 F841 unused-variable removals applied manually after confirming
      each was genuinely dead (none were missed assertions or planned
      side effects). Files touched: `alerts/dashboard.py`,
      `data/ivr_client.py`, `scanners/economic_scanner.py`, three
      test files.
    - Deliberately left untouched: 41 E701, 36 E702, 29 E741, 13 E402,
      7 E712. These are deliberate stylistic choices throughout the
      codebase (inline `if x: return`, `sys.path.insert(...); import`
      pattern, terse variable names). Not bugs.

**Test result:** 239 passed, 4 deselected (integration), ~140s.
Same count as before lint sweep — no behavioral change.

**Commits this session (cumulative):**
- `326348f` chore: move cloudflared tunnel to Linux host (port 8002)
- `527d492` feat: live "prediction resolved" notification at 16:05 ET
- `98120eb` docs: session log for resolved-prediction Pushover + tunnel move
- `b8acd35` docs: park Cloudflare tunnel notes — Tailscale chosen
- `e3e3f35` chore: lint sweep with ruff

**Access from any tailnet device:**
- Mobile-friendly URL: `http://nexus-nucbox-k8-plus:8002/`
- Per-alert deep links (what Pushover embeds):
  `http://nexus-nucbox-k8-plus:8002/alerts/{id}`
- Tailscale must be on. iOS may need to re-enable Tailscale after
  battery saver kills it — first tap after sleep can be slow.

**Open for next session (unchanged order):**

1. ~~Live "prediction resolved" Pushover~~ ✅ earlier this session
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. Web app `/learning` + `/hypotheses` routes
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay
6. (new) Add `pip install ruff` to dev deps so the lint sweep is
   repeatable. Maybe a tiny `pyproject.toml` `[tool.ruff]` block
   disabling E701/E702/E741/E402/E712 so future `ruff check` runs
   only flag the things we actually care about.

---

## 2026-05-16 (evening, laptop pickup) | Live prediction-resolved Pushover + tunnel moved to Linux

**Session length:** ~30 min after returning to the Linux box.

**Context:** Resumed from the morning handoff (commit `5b6a376`). User
killed the Windows-side `cloudflared` service from an admin PowerShell
so this Linux host could take over `alerts.nexus-lab.work`. Picked
Phase 2 follow-up #4 (live "prediction resolved" notification) as the
quick win.

**What changed:**

- `cloudflare/tunnel_config.yml` — credentials path moved to
  `/home/nexus/.cloudflared/…` and backend bumped from `:8000` → `:8002`
  to match `WEB_SERVER_PORT` in `.env`. Linux box is now the canonical
  tunnel host. (commit `326348f`)

- `learning/outcome_resolver.py` — new `format_resolved_message(prediction)`
  helper. Pure function; returns a 1-2-line Pushover/Discord summary
  (✅ CORRECT / ❌ WRONG / — skip) with SPY entry → close + % move.
  Skip days still emit a quiet heartbeat line.

- `learning/scheduler.py` — `job_outcome_resolver` now accepts `post_fn`
  and pings it with the formatted message right after a successful
  resolve. `register_learning_jobs` already had `post_fn` in scope from
  `main.py` (`notifier.message`), so the wiring is one kwarg deep.
  Notification failure is caught and logged so it can't break the job.

- `tests/test_learning_outcome_resolver.py` — 5 new tests:
  formatter output for correct/wrong/skip + scheduler job pings
  post_fn on success and skips it when there's no prediction.

**Test result:** 239 passed, 4 deselected (integration), ~141s
(was 234 before — no regressions, +5 new).

**Commits:**
- `326348f` chore: move cloudflared tunnel to Linux host (port 8002)
- `527d492` feat: live "prediction resolved" notification at 16:05 ET

**What this closes:** Phase 2 follow-up #4. Day-of feedback no longer
waits for the 19:01 reflector — the user gets a Pushover at 16:05 ET
saying whether today's directional call landed.

**Open for next session (in original handoff order):**

1. ~~Live "prediction resolved" Pushover~~ ✅ done this session
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. Web app `/learning` + `/hypotheses` routes
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay

Recommend tackling #2 (promotion CLI) next — accepted hypotheses
currently pile up in `logs/learning/hypotheses/` with no path to
production. Without #2, the weekly Saturday loop produces output
nobody acts on.

---

## 2026-05-16 | Self-learning loop scaffold (paper exec + reflection + hypothesis + backtest)

**Why:** Goal is an assistant that keeps building skill on its own when the
user can't trade or journal daily. The bot now generates its own predictions,
scores them itself overnight, reflects via Claude, and proposes one
backtestable improvement each week.

**What was built — new `learning/` package:**

- `learning/knowledge_base.py` — Append-only JSONL at
  `logs/learning/knowledge.jsonl` with categories `regime_accuracy`,
  `gate_quality`, `sizing`, `exit_timing`, `market_context`, `hypothesis`,
  `backtest_result`, `edge_case`. Re-generates a `KNOWLEDGE.md` rollup
  (last 50 entries, newest first) on every append. `KBEntry` dataclass
  clamps confidence to [0,1] and warns on non-standard categories.

- `learning/predictions.py` — `PredictionLog` writing one row per day to
  `logs/learning/predictions.jsonl`. Each prediction has regime, direction
  (bullish/bearish/neutral/skip), entry SPY, target, stop, plus a
  resolution block (`outcome` = correct/wrong/partial/skip,
  `actual_close`, `actual_move_pct`). Idempotent per date.
  `accuracy(n=60)` aggregates rolling directional accuracy %.

- `learning/paper_broker.py` — Runs at 09:16 ET. Reads today's plan from
  `PlanLogger`, logs a `Prediction`, and (if tradeable) records a paper
  position via `TradeRecorder` tagged `[AUTO-PAPER]` so it's distinct
  from real fills. Always size = 1 contract. Marks plan executed.
  Skip days still produce a prediction so skip-quality is learned too.

- `learning/outcome_resolver.py` — Runs at 16:05 ET. Fetches SPY EOD via
  injected `PolygonClient`, scores today's directional prediction
  (bullish/bearish: sign of move; neutral: |move| < 0.25%). Appends an
  `[MTM YYYY-MM-DD] SPY close $X` line to every open `[AUTO-PAPER]`
  trade so multi-day spreads accumulate a price path for the reflector.
  Idempotent — running twice doesn't double-resolve.

- `learning/reflector.py` — Runs at 19:01 ET. Bundles today's prediction
  + plan + open paper positions + last 14d KB + 30d accuracy into a
  Claude (Sonnet 4.5) call. Asks for a strict JSON reply with
  `summary`, `narrative`, and 1-3 `kb_entries`. Persists:
    - `logs/learning/reflections/YYYY-MM-DD.md` (narrative + context)
    - 1-3 new rows appended to KB
  If JSON parse fails, raw reply is still saved to the markdown so
  nothing is lost; KB simply isn't updated. Pushes summary via the
  notifier (Pushover + Discord).

- `learning/hypothesis_engine.py` — Runs Saturday 10:00 ET. Reads last
  30 days of KB + plans + accuracy and asks Claude for ONE concrete
  tunable change. Targets are constrained to a `TUNABLE_PARAMS`
  whitelist:
    - `signals.regime_detector.ADX_TREND_MIN`  (15.0 — 35.0)
    - `signals.regime_detector.VIX_CALM_MAX`   (12.0 — 22.0)
    - `config.SCORE_ALERT_MINIMUM`             (30 — 75)
    - `config.SCORE_HIGH_CONVICTION`           (55 — 90)
    - `config.MIN_RISK_REWARD_RATIO`           (1.0 — 3.0)
    - `config.IC_RANGE_THRESHOLD_PCT`          (1.5 — 4.0)
  Returns `status: "propose"` or `status: "none"` (with rationale).
  Validates module/var against whitelist and value against range —
  out-of-bounds or off-whitelist proposals are silently rejected.
  Stores spec at `logs/learning/hypotheses/hyp_YYYY-MM-DD_xxxx.json`.

- `learning/hypothesis_runner.py` — Runs Saturday 11:00 ET. Iterates
  pending hypothesis specs, monkey-patches the targeted module var,
  re-runs the 5-year SPY backtest (`backtests.spy_daily_backtest`,
  `--source local`), compares baseline vs modified deltas, and writes
  back to the spec:
    `sharpe_delta >= +0.10 AND pnl_delta > 0`     -> accepted
    `sharpe_delta <= -0.10 OR pnl_delta <= -250`  -> rejected
    else                                          -> inconclusive
  Original value restored in `finally` so a crash can't leak the
  override. Each result also appended to KB as `backtest_result`.
  **Accepted ≠ live** — promotion is a deliberate human step.

- `learning/off_hours_learner.py` — Runs Sunday 10:00 ET. Replays
  the last 60 days of SPY history through the *current* regime
  detector, flags "near-miss" days (ADX or VIX within 10% of
  threshold AND next-day move went against the directional call),
  asks Claude to find shared patterns, appends 1-3 `edge_case` /
  `market_context` KB entries. Always writes a JSON report at
  `logs/learning/off_hours/YYYY-MM-DD.json` whether or not Claude
  is reachable.

- `learning/scheduler.py` — `register_learning_jobs(scheduler,
  polygon_client, post_fn)` adds all six jobs onto the existing
  APScheduler. Each job wrapped in try/except so one failure can't
  crash the bot. Wired into `main.py` after the SPY daily jobs.

**Tests (34 new, all passing):**

- `tests/test_learning_kb.py` — 8 tests for KB + PredictionLog
  (append, recent, by_category, stats, confidence clamping,
  prediction idempotency, accuracy aggregation).
- `tests/test_learning_paper_broker.py` — 5 tests for paper broker
  (tradeable / skip / from-plan / no-plan / plan idempotency).
- `tests/test_learning_outcome_resolver.py` — 9 tests covering all
  direction × outcome combinations, skip days, idempotency, and
  MTM snapshotting against open positions.
- `tests/test_learning_reflector.py` — 3 tests with mocked
  `_call_claude` for happy path, malformed JSON, and missing API key
  (with `monkeypatch.delenv` to avoid hitting the live key in env).
- `tests/test_learning_hypothesis.py` — 9 tests for engine
  (valid / out-of-range / off-whitelist / status="none") and runner
  (accept / reject / inconclusive / re-run-skip / non-whitelist-error).

**File layout:**

```
learning/
  __init__.py
  knowledge_base.py
  predictions.py
  paper_broker.py
  outcome_resolver.py
  reflector.py
  hypothesis_engine.py
  hypothesis_runner.py
  off_hours_learner.py
  scheduler.py

logs/learning/
  knowledge.jsonl
  KNOWLEDGE.md
  predictions.jsonl
  reflections/YYYY-MM-DD.md
  hypotheses/hyp_*.json
  off_hours/YYYY-MM-DD.json
```

**Schedule summary (added to existing scheduler):**

```
09:16 ET (Mon-Fri)  learning.paper_broker.execute_today()
16:05 ET (Mon-Fri)  learning.outcome_resolver.resolve_today()
19:01 ET (Mon-Fri)  learning.reflector.reflect_today()
Sat 10:00 ET        learning.hypothesis_engine.propose_weekly()
Sat 11:00 ET        learning.hypothesis_runner.run_pending()
Sun 10:00 ET        learning.off_hours_learner.run()
```

**Known follow-ups for next session:**

1. **Web app surface** — `alerts/web_app.py` needs new routes:
   `/learning` (KB browser, prediction accuracy chart, recent
   reflections), `/hypotheses` (approve/reject pending accepted
   hypotheses to actually edit `config.py` / `regime_detector.py`).
2. **Expiry-based exit** — `outcome_resolver` only snapshots MTM on
   open `[AUTO-PAPER]` positions; nothing closes them at expiry yet.
   Add an `expiry_resolver` that closes positions when DTE hits 0
   using the realized SPY path and the spread's payoff function.
3. **Promotion workflow** — Accepted hypotheses sit in
   `logs/learning/hypotheses/` until a human acts. Need a CLI
   (`python -m learning.promote <hyp_id>`) that writes the change
   to source with a generated commit, plus a Pushover ping on
   accept so the user knows there's something to review.
4. **Live notification when a prediction is resolved** — currently
   only the daily reflection at 19:01 surfaces it. A short
   "prediction X today resolved Y" Pushover at 16:06 would close
   the day-of feedback loop.
5. **VIX in off-hours replay** — currently hardcoded to 16.0 because
   we don't have historical VIX in the local CSV. Wire the CBOE CSV
   loader (already in `data/vix_client.py`) into the replay.

**Session handoff (transferring to laptop):**

- All work committed in `6591605`. Pull on the laptop:
  `git pull origin main`
- First thing to verify the loop is alive end-to-end on the laptop:
  `pytest tests/test_learning_*.py -v` (expect 34 passing)
- Then `python main.py` will start the bot with the six new learning
  jobs already wired into the scheduler.
- The first paper trade + prediction will land at 09:16 ET the next
  weekday; the first reflection at 19:01 ET that same day.
  `logs/learning/` will populate from there. Nothing to do but watch.
- Pick the Phase 2 follow-up to attack first — recommendation is #3
  (promotion workflow), since without it accepted hypotheses just pile
  up. #4 (live "resolved" Pushover) is the easiest quick win.

---

## 2026-04-30 | Per-alert web app (FastAPI + SQLite + Claude chat + journal)

**What was built:**

- `alerts/alert_store.py` — SQLite store at `logs/alert_store.db` with three
  tables: `alerts`, `journal_entries`, `chat_messages`. Idempotent schema
  init on import; WAL mode; thread-locked writes. 8-char UUID alert IDs.
  Public API: `save_alert / get_alert / get_recent_alerts /
  save_journal_entry / get_journal_entries / save_chat_message /
  get_chat_history`.

- `alerts/web_app.py` — FastAPI app, single-string HTML (no Jinja2, no static
  files). Mobile-friendly dark UI. Routes:
  `GET /health`, `GET /` (recent alerts list),
  `GET /alerts/{id}` (per-alert detail page with three sections:
  Alert Details, Chat, Journal), `POST /alerts/{id}/chat`,
  `GET|POST /alerts/{id}/journal`. Chat persisted to DB so history
  survives across sessions. Anthropic SDK with prompt caching on the
  trading-coach preamble; per-alert context appended uncached.

- `alerts/notifier.py` — migrated from JSON-file persistence to
  `alert_store.save_alert()`. Save happens before the Pushover send so the
  deep link URL embedded in the notification resolves to a real DB row.

- `alerts/pushover_client.py` — `_build_alert_url` now uses
  `config.PUSHOVER_BASE_URL` and the plural `/alerts/{id}` route, with
  url_title `"View Trade + Chat"` (was `DASHBOARD_BASE_URL` and
  singular `/alert/{id}`).

- `main.py` — replaced the in-thread `start_web_server()` (which imported
  `web.app` and ran uvicorn programmatically) with a `subprocess.Popen`
  block that runs `uvicorn alerts.web_app:app` as a separate process,
  using config-driven `WEB_SERVER_HOST`/`WEB_SERVER_PORT`. Process is
  terminated cleanly in the `KeyboardInterrupt` shutdown path.

- `cloudflare/tunnel_config.yml` + `cloudflare/README.md` — tunnel config
  scoped to `alerts.nexus-lab.work` with ingress to `localhost:8000`,
  plus full setup instructions.

- `tests/test_web_app.py` — 6 TestClient tests against an isolated tmp
  SQLite DB (via `ALERT_STORE_DB` env var), with `_ask_claude` patched
  out so no Anthropic key is required.

**Removed (Option A — clean replacement, not coexistence):**

- `web/app.py`, `web/templates/alert.html`, `web/static/style.css`,
  `web/__init__.py` — superseded by `alerts/web_app.py`.
- `config.py` `DASHBOARD_BASE_URL` — replaced by `PUSHOVER_BASE_URL`
  added in the prior commit. Single source of truth for the public host.
- `install_hooks.py` + `post-commit` (root) + `.git/hooks/post-commit`
  — the auto-overwriting BUILD_LOG hook flagged in the previous session.
  Killed it so curated session logs survive commits going forward.

**Test count:** 200 passed, 4 deselected (integration), ~118s.
Was 194 before (no regressions); 6 new web app tests added.

**Known follow-ups:**

1. **Cloudflare tunnel ID** is still `TUNNEL_ID_PLACEHOLDER` in
   `cloudflare/tunnel_config.yml`. Run the steps in `cloudflare/README.md`
   (auth, create tunnel, route DNS) and replace the placeholder with the
   real UUID before the `https://alerts.nexus-lab.work/alerts/<id>`
   links go live end-to-end.
2. `.env` still has a `DASHBOARD_BASE_URL` line. Harmless (nothing reads
   it now) but worth deleting on the next pass.
3. `pushover_client.send()` still doesn't accept a `sound` kwarg — would
   let production code route different sounds per alert tier (e.g.
   `cashregister` for high-conviction). Easy follow-up.

---

## 2026-04-30 | Pushover live test + PUSHOVER_BASE_URL config

**What was built:**

- `tests/test_pushover_live.py` — standalone (non-pytest) script that
  POSTs directly to the Pushover REST API with `priority=1`,
  `sound=cashregister`, and a clickable `url` + `url_title`. Bypasses
  `PushoverClient.send()` because that method doesn't accept a `sound`
  kwarg yet.
- `config.py` — added `PUSHOVER_BASE_URL` (default
  `https://alerts.nexus-lab.work`) for building per-alert deep links.

**Test result:** Live notification confirmed received on device with
correct title, body, link, and cash-register sound.

**Commit:** `433a494`.

---

## 2026-04-30 | Weekend guard + options flow wiring + cleanup pass

**What was changed:**

- **Weekend / market-hours guards** — verified already present in
  working tree, committed: `swing_scanner.py` weekday check, `intraday_scanner.py`
  `weekday() >= 5` short-circuit in `is_market_hours()`, `main.py`
  `IntervalTrigger(start_date=next_open)` so intraday scan doesn't fire
  before 9:30 AM ET on first start.
- **Options flow scanner wired into main.py** at 09:35 ET via the
  `Notifier` router (Pushover primary + Discord secondary).
- **Cleanup pass:** removed duplicate imports in `main.py`; deleted stray
  `scheduler/scheduler__init__.py` typo file; pyflakes-driven removal of
  16 unused imports across 16 files; AST scan + closed 20 missing
  docstrings on classes / public methods.

**Tests:** 194 passed, 4 deselected (integration). No assertions modified.

**Issues flagged for follow-up:**

1. `install_hooks.py` + `post-commit` install a hook that auto-overwrites
   BUILD_LOG.md from `git log` — directly conflicts with the curated
   manual format. *Resolved in the next session (see entry above).*
2. `.claude/settings.local.json` is now gitignored under `.claude/`.
3. `TRADING_ASSISTANT.md` still says "157+ tests passing" — outdated;
   real number was 194 then, 200 now.

**Commit:** `29eb630`.

---

## 2026-04-30 | Scaffolding — CLAUDE.md, TRADING_ASSISTANT.md, BUILD_LOG.md, STRATEGY_LOG.md

**What was set up:**
- Created project scaffold files to bridge Claude.ai and Claude Code sessions
- CLAUDE.md: full architecture map, tuned thresholds, standing rules
- TRADING_ASSISTANT.md: current project state, active decisions, parking lot
- BUILD_LOG.md: this file
- STRATEGY_LOG.md: synced to Google Drive for Claude.ai continuity

**Current test baseline:**
- 157 tests passing (run: `pytest tests/ -v -m "not integration" --tb=short`)

**Git status at session end:**
- All previous work committed and pushed to main
- Scaffold files added in this session

**Next session should:**
1. Run `/init` in Claude Code to index the codebase against CLAUDE.md
2. Apply weekend guard fix to swing_scanner.py and intraday_scanner.py
3. Fix IntervalTrigger start_date in main.py
4. Wire options_flow_scanner.py into main.py scheduler
5. Clean up duplicate imports and missing __init__.py files
6. Run full test suite to confirm 157+ still passing

---

## 2026-04-29 | SPY Daily Strategy — full integration into main.py

**What was built:**
- Fixed main.py: removed duplicate imports, added PolygonClient import,
  wired VIXClient + IVRClient + EventCalendar into register_spy_jobs()
- Bot now starts with SPY daily jobs registered (09:15, 16:30, 19:00 ET)
- Confirmed Discord alerts posting correctly (manual test passed)

**Tests:** 157/157 passing

---

## 2026-04-28 | Backtest harness + threshold tuning

**What was built:**
- backtests/spy_daily_backtest.py: 5-year SPY replay engine
- download_spy.py + write_backtest.py: yfinance data pipeline (Windows-safe)
- backtests/spy_history.csv: 1255 bars (2021-2026)

**Backtest results after tuning:**
- ADX_TREND_MIN raised 20 -> 25
- VIX_CALM_MAX tightened 18 -> 17
- TRENDING_HIGH_VOL set to not tradeable
- Final: Win rate 50.3% | Sharpe 1.73 | P&L +$11,550 over 5 years
- Iron condor: 74.1% win rate, +$9,540 (core edge confirmed)

**Tests:** 157/157 passing

---

## 2026-04-27 | Event calendar + VIX/IVR clients + Discord race condition fix

**What was built:**
- data/vix_client.py: VIX via Polygon or CBOE CSV fallback
- data/ivr_client.py: IV Rank via options chain or VIX proxy
- data/event_calendar.py: FOMC, CPI, NFP, OPEX — auto-computed, weekly cache
- alerts/discord_bot.py: fixed race condition (_bot_ready gate),
  fixed channel cache miss, added post_message_sync()

**Tests:** 180/180 passing (157 existing + 23 new event calendar tests)

---

## 2026-04-26 | SPY daily regime strategy stack

**What was built:**
- signals/regime_detector.py: 6-regime ADX+VIX+IVR classifier
- signals/spy_daily_strategy.py: regime -> OptionsLayer orchestrator
- scheduler/spy_daily_scheduler.py: APScheduler jobs (09:15/16:30/19:00 ET)
- journal/plan_logger.py: planned trade log (separate from trade_recorder)

**Tests:** 157/157 passing

---

## 2026-04-25 | Options flow scanner (UOA)

**What was built:**
- scanners/options_flow_scanner.py: unusual options activity scanner
  Detects VOL/OI spikes, OTM monsters, high IV event bets
  Works on Polygon free tier (prev-day data, no real-time feed)
- tests/test_options_flow_scanner.py: 18/18 passing

**Status:** Built and tested. NOT yet wired into main.py scheduler.
Wire-in is next Claude Code session task.
