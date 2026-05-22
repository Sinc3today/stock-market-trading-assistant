# Intraday-Touch Exit Backtest — Design Spec

**Date:** 2026-05-22
**Status:** Approved design, pending implementation plan
**Author:** brainstormed with Claude Code

---

## Problem / Motivation

Both the live exit manager (`learning/exit_manager.py`, scheduled 16:08 ET) and the backtest realistic pricer (`backtests/realistic_pricing.py`) currently mark open spreads only at the **daily close**. A position that hits +90% of max profit at 11:30 AM and drifts back to +30% by 4:00 PM is invisible to both: the 16:08 mark sees +30%, the 70% profit-target gate doesn't fire, the trade stays open. The intraday peak is never harvested.

This is leaving money on the table on the upside, and underestimating achievable P&L in the backtest. The conventional live fix is a GTC limit order at the profit target (the broker fills the moment the spread trades there). For the paper model and backtest, the equivalent is checking each day's intraday HIGH and LOW marks — not just the close.

We measure the effect with a walk-forward backtest first, then port to live if (and only if) it earns its place.

## Goals

- Add an *opt-in* intraday-touch exit mode to `backtests/realistic_pricing.simulate_trade` that re-marks the spread at the day's HIGH, LOW, and CLOSE, exiting at the first mark that hits the profit target.
- Build a walk-forward harness that compares **intraday-touch exit vs daily-close exit** on identical entry days across the full 5yr SPY tradeable population.
- Evaluate the measured result against **six named ship-bar presets** that each probe a distinct failure mode, so we learn the *shape* of the answer — not just a binary ship/shelve.
- Preserve existing live behaviour: default off; live `ExitManager` unchanged in this project.

## Non-Goals (deferred)

- **Live `ExitManager` wiring.** If the backtest passes the binding bar, the live port is the next project. Not built here.
- **True intraday (5-min) bars.** Option B from the design discussion. The daily HIGH/LOW under-counts iron-condor touches (their best intraday mark is at minimum-deviation, not the extremes); the conservative bias is acknowledged here. Richer intraday-bar modeling is the upgrade path if a "close but no pass" result implicates condors.
- **New config flags for the exit mode itself.** The opt-in lives as a function parameter (`intraday_touch: bool = False`) — the walk-forward script drives both modes. The only new `config.py` constants are the three ship-bar floors (so they're tunable, not magic numbers).

## Locked Decisions

| Decision | Choice | Why |
|---|---|---|
| Granularity | **Daily HIGH / LOW / CLOSE re-mark** (three points per day) | Free — daily OHLC already in our data. Exact for directional spreads; conservative for condors (acknowledged limitation, not a bug). |
| Live `ExitManager` change | **None in this project** | Measure before changing live behaviour. Standard discipline. |
| Symmetry on stops | **Profit-target touch only; stop stays daily-close** | Matches live: no broker-side hard stop exists, so live-realism = intraday-touch on the upside only. |
| Same-day exit | **Not applicable** | Entry is at the entry day's **close**; that day's high/low already happened *before* the trade existed (look-back, not look-forward). The touch loop correctly starts at `entry_idx + 1` — same as today — but now checks 3 marks per day instead of 1. |
| Comparison set | **All 5yr SPY tradeable plays, all five regimes** | The plays our regime detector actually produces (bull/bear debit/credit + iron condor). |
| Walk-forward split | **60/40 by date** | Matches the iron-condor and meta-labeling studies for consistency. |
| Ship-bar presets | **Six** (see §6) | Probes magnitude, attribution, IS-dependence — not just gradients. |
| Binding ship decision | **`default-2σ`** | Other five presets are learning context; only this one auto-ships. |

## Architecture

A single optional parameter on the existing pricing engine, plus a new standalone comparison script. No new modules in production paths.

```
backtests/realistic_pricing.simulate_trade(..., intraday_touch=False)
                       │
                       ▼ (when True)
        re-mark at day's HIGH, LOW, CLOSE  ─►  exit at first to hit target
                       │
                       ▼
backtests/intraday_touch_wf.py  (new comparison script)
        ├─ runs same backtest twice (touch off / on)
        ├─ joins per-trade outcomes on entry date
        ├─ aggregates Δ$/trade, attribution %, IS/OOS, per-regime
        └─ evaluates against the 6 presets  ─►  verdict matrix
```

## Components

### `backtests/realistic_pricing.simulate_trade` (modified)
Add `intraday_touch: bool = False` parameter (last keyword, default off preserves every existing caller). Inside the day-walk loop:
- When `intraday_touch=True`, compute marks at `spy_df.loc[d, "high"]`, `low`, and `close` using the existing `_net_value` BS engine.
- For credit structures, the best mark is the **lowest cost-to-close**; for debits, the **highest proceeds**.
- If the best mark hits `profit_target_pct`, exit at that mark with `exit_reason="target_intraday"`.
- Stop check (`stop_loss_frac`) stays on the daily close — explicit and documented.
- Loop still starts at `entry_idx + 1`; no same-day check (entry is at the entry day's close, so that day's high/low pre-date the trade).

### `backtests/intraday_touch_wf.py` (new)
Standalone script (no library API). Loads 5yr SPY+VIX, runs `SPYBacktest` for regime classifications, then prices every tradeable day twice (touch off, touch on) on identical entry dates, joins the two trade frames on date, and produces:
1. Aggregate metrics: OOS Δ$/trade, OOS Δ$/trade as % of baseline, attribution (% of OOS exits via `target_intraday`), IS Δ$/trade.
2. Per-regime breakdown of Δ$/trade (n + Δ per regime).
3. **Verdict matrix** evaluating the measured result against the six presets.

### `config.py` additions (3 constants only)
```python
INTRADAY_TOUCH_SHIP_MIN_DOLLAR = 25.0   # binding statistical floor ($/trade) for default-2σ preset
INTRADAY_TOUCH_SHIP_MIN_FRAC   = 0.10   # binding scale floor (10%) for default-2σ preset
INTRADAY_TOUCH_SHIP_MIN_ATTRIB = 0.15   # binding attribution floor (15%) for default-2σ preset
```
The other five presets are hard-coded inside `intraday_touch_wf.py` as their own table — they're learning-context presets, not tunables.

## The Six Ship-Bar Presets

| Preset | Stat floor ($/trade) | Scale floor (%) | Attribution floor (%) | IS sanity | What it probes |
|---|---|---|---|---|---|
| **strict-3σ** | $40 | 15% | 25% | on | Is the effect obvious? (1-in-370 random) |
| **default-2σ** | $25 | 10% | 15% | on | **Binding decision.** Real by conventional standards. |
| **lenient-1.5σ** | $15 | 5% | 10% | on | Real-but-smaller effect? |
| **research-1σ** | $10 | 5% | 5% | off | Any directional signal? *Not for shipping.* |
| **attribution-strict** | $20 | 10% | 30% | on | Is the gain really driven by the new exits firing? |
| **oos-only** | $25 | 10% | 15% | off | How much does the verdict depend on IS sanity? |

Verdict report (printed):
```
=== Intraday-touch exit — walk-forward verdict ===
measured:  IS Δ=$XX  OOS Δ=$YY  (ZZ% of baseline)  attribution=AA%

preset                Δ$  scale  attrib   IS    verdict
strict-3σ             ✓✗    ✓✗     ✓✗     ✓✗    SHIP/no
default-2σ            ...                       SHIP/no   <- BINDING
lenient-1.5σ          ...                       SHIP/no
research-1σ           ...                       SHIP/no
attribution-strict    ...                       SHIP/no
oos-only              ...                       SHIP/no

per-regime Δ$/trade:
  trending_up_calm    n=XX   Δ=$YY
  ...
```

## Honesty Caveats (baked into the spec, surfaced in the report)

- **Conservative bias on iron condors.** Daily HIGH/LOW under-counts condor touches (best mark is at minimum-deviation). If condors drive a fail-or-borderline result, the upgrade path is true intraday bars (option B from the brainstorm).
- **Per-trade samples are not independent.** Overlapping entries on the same market move are correlated. The walk-forward 60/40 split is what gives us OOS honesty, not the raw n.
- **Conservative-model + strict-bar pairing is intentional.** Pairing a conservative model with a loose bar is the trap to avoid.

## Patterns We're Looking For (from the brainstorm)

| Pattern | What it tells us |
|---|---|
| strict-3σ ships | Slam-dunk; live wiring is the obvious next step. |
| default ships, strict doesn't | Real but moderate edge; per-regime view + condor-bias informs the live call. |
| lenient ships, default doesn't | Borderline; defensible to lower production bar with eyes-open. |
| research ships, lenient doesn't | Weak directional signal; not shipworthy, but a flag for option-B richer data. |
| research fails | Definitively no edge at any bar. Shelve. |
| oos-only ships, default fails | Lucky-OOS-window — IS gate did its job. Don't override. |
| attribution-strict fails when default ships | Improvement isn't actually from the new exits — incidental reshuffling. Skeptical. |

## Testing Strategy

- **`simulate_trade` opt-in correctness:** synthetic series where daily high clearly hits the profit target but the close does not → exits with `target_intraday`, smaller `days_held` than the no-touch baseline.
- **Parity preserved:** with `intraday_touch=False` (default), output is byte-identical to today's behaviour. Tested across all play types.
- **Pathological case:** high == low == close → behaviour matches daily-close exactly (no spurious touches).
- **Walk-forward harness smoke test:** runs end-to-end on synthetic mini-data, emits a verdict matrix.
- **Verdict logic:** a hand-built measured-result + preset table → expected ship/no for each preset.

## File Inventory

| File | Action | Lines (approx) |
|---|---|---|
| `backtests/realistic_pricing.py` | Modify (add `intraday_touch` param + intraday-touch logic in the loop) | +25-40 |
| `backtests/intraday_touch_wf.py` | Create | ~150 |
| `tests/test_realistic_pricing.py` | Extend (intraday-touch tests) | +30-40 |
| `tests/test_intraday_touch_wf.py` | Create (verdict logic + smoke test) | ~80 |
| `config.py` | Add 3 constants | +5 |
| `BUILD_LOG.md` | Append verdict entry (after Task N runs the walk-forward) | +20 |

## Next Project (out of scope, captured for follow-up)

If `default-2σ` ships, the next project is: **port intraday-touch into `learning/exit_manager._evaluate`** (re-mark at today's high/low/close in the same way, exit at first touch). Single small task. Spec'd separately when needed.
