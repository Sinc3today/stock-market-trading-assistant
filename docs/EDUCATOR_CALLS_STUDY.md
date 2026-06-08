# Educator-Calls Study — score their predictions against our data; audit our blind spots

**Date:** 2026-06-08 · **Modules:** `tools/transcript_miner.py` (capture) + `backtests/educator_calls_scorer.py` (score) · Research only.

## The reframe
The first pass mined transcripts for ready-made *rules* and found almost none — the content is
discretionary macro commentary. That was the wrong axis. The real value: each video is a **dated,
falsifiable forecast** by an experienced trader. So instead of "did they hand us a rule," we ask:

1. **Were they right?** — score each forward call against the SPY/QQQ/VIX/etc. history we already hold.
2. **Did we see it too, or miss it?** — compare their *market read* to our regime classifier on that
   date, and flag the *non-price signals* they lean on (sentiment, positioning, Fed/liquidity,
   intermarket, options flow) that our pure-technical system may not track = **blind-spot candidates**.

A verified prediction is worth more than a static rule: it tells us which discretionary reads have
edge, and where our system is blind.

## Pipeline
- **Capture** (`transcript_miner.py`, local gemma3:4b, free): each curated video is tagged with its
  recording date (`<!--vid:ID date:YYYY-MM-DD-->`) and the prompt extracts pipe-delimited CALL lines
  `CALL | instrument | direction | horizon | level | reasoning`, plus MARKET READ and NON-PRICE SIGNALS.
- **Score** (`educator_calls_scorer.py`): `parse_kb` → `score_call` (forward-return over the horizon
  window vs a directional threshold) → `aggregate` (hit-rate by instrument / horizon / direction).
  Coarse directional judge over a window — a **lead-finder, not a P&L sim**. Honest by design.

## Mechanics proof (real data, 2025-06-27 "PCE & ALL TIME HIGHS")
Representative reads from that video, judged against OUR real history:

| date | inst | dir | horizon | actual fwd | verdict |
|---|---|---|---|---|---|
| 2025-06-27 | SPY | up | 1-2wk | +1.61% | HIT |
| 2025-06-27 | SPY | up | months | +3.42% | HIT |
| 2025-06-27 | QQQ | up | weeks | +2.93% | HIT |
| 2025-06-27 | VIX | down | days | +7.11% | MISS |

3 hit / 1 miss. The equity reads landed; the **vol/timing call missed** — exactly the kind of
divergence worth a second look (did our regime classifier read vol differently that day?).

## What this is / isn't
- **Is:** a falsification-first lens to find *which* reads (which traders, regimes, horizons,
  instruments) actually have predictive edge — leads to formalize and walk-forward, plus a
  blind-spot audit of our coverage.
- **Isn't:** a verdict. Directional hit/miss over a window ignores path, sizing, and option
  structure. A high educator hit-rate in some bucket is a *lead*, not an edge — it graduates to a
  real WF study (same discipline as the breadth lead).

## Status
Pipeline built + tested (10 unit tests). Live gemma corpus build is queued — it stalls under
current nucbox CPU contention (an external qwen2.5vl:7b job); resumable, runs when the box frees.
The scorer is ready to run against the KB the moment the corpus exists:
`python -m backtests.educator_calls_scorer`.
