# System hardening plan — getting SMTA to a confident, functional state

**2026-09-07.** Written after a five-way audit prompted by a fair question:
*"Just because we have disprovers and validators doesn't mean we should always
have issues with our system."*

That is correct. **Eight defects found in one day is not a validator success
story — it is a quality signal.** Two of the eight were in code written that
same day. This document is the diagnosis and the fix.

---

## Verdict

**The system is not dangerous. It is unreliable as an instrument.**

Real-money exposure is two live SPY condors. Nothing here is quietly losing
large sums. What it *is* doing is producing numbers that cannot be fully
trusted — and every strategy decision rests on those numbers. That is worse for
the stated goal (a confident investment strategy) than a straightforward loss
would be, because it is invisible.

---

## Root cause, in one sentence

> **No decision in this codebase has a single owner, and nothing can enumerate
> the places a decision is made** — so every fix is partial, every guard applies
> only where someone remembered it, and the tests are structurally incapable of
> noticing.

Four independent measurements of the same thing, all from 2026-09-07:

| evidence | result |
|---|---|
| The "one source of truth" credit/debit fix, shipped that morning | covered **3 of 13** classifiers |
| `ENFORCE_CONCENTRATION_GUARD = True` | enforced in **1 of 6** opening paths |
| Time-to-expiry convention | **6 different** implementations; the 0DTE fix reached 1 caller |
| Test suite shape | **~1 mock per 8 assertions**, **zero** cross-module agreement tests |

The last line is the load-bearing one. With that much mocking and no agreement
tests, **two modules disagreeing is not an observable event.** The suite cannot
fail for the reason things actually break.

## The unifying defect shape

Nearly every P1 found is the same mistake wearing different clothes:

> **A missing price is not a price.**

Where the code cannot determine a value it substitutes a plausible-looking
constant — `0.0`, `1.00`, `16.0`, `18.0`, `20.0`, `50`, `99` — and that constant
becomes a **recorded number** indistinguishable from a real one. The correct
behaviour already exists and is used in exactly two places
(`intraday_structure_builder`, `rh_session`): **return `None`, and let the
caller refuse to open, refuse to close, or mark the record `unscored`.**
`log_exit` already supports that convention. Almost nothing uses it.

---

## Firing right now, on recorded data

Each verified by execution against the live tree or the live journal.

| # | Defect | Evidence |
|---|---|---|
| 1 | **`rh_sync:376` books real-money exits at the ENTRY price** when any leg lacks a mid (routine for a condor's long wings). Produces a fabricated `$0 / breakeven`. | 3 of 4 `[RH-SYNC]` exits; **2 live condors exposed** |
| 2 | **`paper_broker:521` returns a hardcoded `$1.00`** when it cannot read the spread premium — and that becomes the recorded entry price. | **18 of 109 trades** at exactly `$1.00`; **11 disciplined**; 14 with booked P&L. `F1A205B7` shows `max_profit=200` (true credit ~$2.00) booked at $1.00 |
| 3 | **Unscored trades counted as losses.** The A1 fix correctly stopped fabricating `$0`, but no aggregator was updated — so the corruption changed sign instead of going away. | headline win rate **49.4%** vs honest **70.7%**; 25 unscorable trades sitting in the denominator |
| 4 | **`ivr_client` and `_fetch_spot` are reading a stale price today.** `limit=N` returns the **oldest** N bars, not the newest. | Sep 1 (765.16) instead of Sep 3 (770.19) — 0.65% stale. **IVR feeds the regime classifier** |
| 5 | **`trades.json` lost-update race.** Four jobs read-modify-write with no lock, one in a separate process (uvicorn), every trading day at 09:45. | **29 same-minute multi-writes** in the journal; survived on interleaving luck |

## Armed, not yet fired

| # | Defect | Exposure |
|---|---|---|
| 6 | **BWB expiry settlement is sign-inverted + clamped** (`expiry_resolver:172`) — books peak profit as a loss and max loss as the biggest win. The identical bug fixed in `exit_manager` that morning, in a file never opened. | **17 open BWBs**, 18 days to nearest expiry. Fires on promotion — and the BWB test is at 18 trades against a 15-trade bar |
| 7 | **`dipbuy_forward:139` case-sensitive leg action.** `condor_calc` writes `"BUY"`, `options_layer` writes `"buy"` — every lowercase leg prices as a short. | ~$2,400 error on one reproduced trade |
| 8 | **`_get_cost_basis` never got the A3 fix** — `pnl_pct` 100× too large for variant names. Feeds `reflector`, which hands it to Claude as ground truth. | 31 trades; a recorded `pnl_pct` of **15,890%** |
| 9 | **`VIXClient.get_current()` returns `20.0` and never `None`**, so three fallback layers and a `if vix is None` guard are all dead code. 20.0 also straddles `VIX_CALM_MAX=18`, so an outage deterministically flips the regime. | every marked exit during an outage |
| 10 | **`options_chain._safe_mid` returns `0.0`** for an unpriced leg, inflating credit by the wings — produces structurally impossible risk-free trades that pass the R/R gate. | `DB8869CD`: `max_profit 0 / max_loss 500` |

## Silent blindness

- **APScheduler errors and misfires go to stderr only** — no stdlib→loguru
  bridge, so `loop_health` can never see an uncaught job failure or a dropped
  09:45 fire. **A whole trading day can vanish invisibly.**
- **Nothing monitors whether the bot is still opening positions.** Predictions
  stay fresh on skip days, so a permanently-stuck gate is invisible
  **indefinitely** by any automated means.
- **A once-daily job can fail every day forever** without reaching the 3-per-24h
  alert threshold. Its fallback (`post_fn`) routes to `logger.info` and never
  reaches the phone.
- **`condor_short_strike_touch` and `forced_close_*` are configured `True`,
  asserted by tests, and never read by `_evaluate`.** Config and tests both
  describe a feature that does not exist.

---

## The plan

### Phase 0 — Stop the silent losses *(small, mechanical, highest value/hour)*
1. `fcntl.flock` around `TradeRecorder._load`/`_save` — must be file-level; a
   threading lock cannot fix a cross-process race.
2. `rh_sync:376` — close as `unscored`, never at the entry price.
3. `paper_broker._spread_price` — return `None` and refuse the open; make
   `OptionsLayer.analyze()` publish a numeric `net_credit`/`net_debit`.
4. Exclude `pnl_dollars is None` from every win-rate denominator
   (`get_summary_stats`, `paper_trade_stats`); surface `unscored: N`.
5. `polygon_client.get_bars` — pass `sort="desc"`; assert the newest bar is the
   last trading day.
6. `VIXClient.get_current()` returns `None` on total failure.
7. stdlib-logging → loguru interception; add journal-staleness and
   no-open-streak checks to `loop_health`.

### Phase 1 — Collapse the duplication *(the actual cure)*
8. One `signals/structures.py` owning **convention, valuation, cost basis, and
   time-to-expiry**. Delete the 10 remaining inline credit/debit lists and the
   6 competing `t_years` expressions.
9. One `should_open()` gate every opening path must call, so the four risk
   guards apply by construction rather than by memory.
10. One parameterized `ForwardTest` replacing five near-duplicate generators
    (`resolve_*` bodies are 75–86% identical).

### Phase 2 — Make partial fixes impossible *(enumeration)*
11. **Vocabulary-totality test:** every `strategy=` literal any producer can
    emit is understood by every consumer. *Fails today.*
12. **Cross-implementation agreement test:** recorder ≡ exit_manager ≡ rh_sync,
    including sign, over the whole vocabulary. *Fails today.*
13. A test asserting **no module string-matches a strategy name**.
14. **Round-trip integration test:** open → mark → close → journal → summary →
    scorecard. Covers `journal/performance.py`, which has zero tests.
15. Run `forward_audit.run_all()` against the live journal on a schedule and
    fail on any P1.
16. A real `tests/conftest.py` isolating `config.LOG_DIR` (61 files hand-roll it
    four different ways today).

### Phase 3 — Subtract
17. 0DTE still **fetches option chains and writes journal records** for a
    shelved strategy (32 rows). Route it off before the chain fetch.
18. Weekly meta-recalibration runs a full backtest for a **disabled** feature,
    calling a method that does not exist.
19. Delete dead knobs, orphaned tunables (`exit_manager.PROFIT_TARGET_PCT` is
    whitelisted for hypothesis tuning but drives nothing — the loop can
    "accept" a change that never ships), and the touch-exit rules that are
    configured but never consulted.
20. Extend the CPI static fallback — it **ends 2026-12-08**, after which the bot
    trades into every CPI print silently. Same fuse as the FOMC one already
    fixed; CPI was left behind. `US_MARKET_HOLIDAYS` ends 2026 likewise.

### Phase 4 — Resume strategy work
On an instrument that can be trusted.

---

## Recommendation

**Pause new strategy work until Phase 0–2 are done.**

Everything built on 2026-09-06/07 — the 14/21DTE rungs, the calm-calibration
instrument, the exit-rule changes — rests on measurements taken with this
instrument. Extending it now compounds the problem: `ladder_forward.py` became a
**sixth** copy of a pattern we had just proved propagates defects, complete with
the missing risk guards, because it was written by copying its siblings.

We cannot reach "confident" by finding bugs faster. We reach it by removing the
places bugs can hide.

## What this does NOT change

The strategy findings still stand — they were measured with a consistent (if
imperfect) instrument, and the OOS/haircut/commission discipline was real. What
is now in question is **precision, not direction**. Re-run the ladder study
after Phase 1 and expect the numbers to move somewhat and the ordering to hold.
