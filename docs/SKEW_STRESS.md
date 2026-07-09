# Skew / credit-quality stress test

**Question (audit T2#6, 2026-07-09):** our backtests price condors with flat-vol
Black-Scholes (VIX as sigma for every strike, r=0, no skew). If real credits are
worse than the model says, does the 5-yr condor edge survive?

**Method:** `backtests/skew_stress.py` — the exact condor backtest (same 385
regime-gated entry days, same 70%/21-DTE management, same frictions), with only
the ENTRY credit haircut. Exit marks stay model-priced, so this is the harsh,
one-sided version of the stress.

## Result — the edge is hypersensitive to credit quality

| entry-credit haircut | win% | total P&L | return on capital |
|---|---|---|---|
| 0% (model) | 66.8% | **+$14,765** | +7.6% |
| **−10%** | 46.8% | **−$7,923** | −3.7% |
| −15% | 36.6% | −$19,268 | −8.5% |
| −20% | 29.1% | −$30,612 | −12.8% |
| −25% | 19.2% | −$41,957 | −16.8% |

A 10% haircut to what we collect at entry flips the whole 5-year edge from
strongly profitable to losing. **The backtested P&L lives or dies on credit
realism — it is NOT robust to pricing error.**

## The counter-evidence — real fills so far are RICHER than the model

The direction of the flat-vol bias can't be settled from theory (skew makes the
put side richer to sell but the put wing costlier to buy; VIX vs strike-level IV
cuts both ways). The empirical evidence we have:

- The user's real July condor filled at **$1.55 vs the bot's $1.00 model mark —
  +55% better** (journal E7350D4A, `bot_mark` field).
- The Aug condor's real fill ($1.45/share) is likewise in the model's ballpark
  or better for the comparable structure.
- Round-trip spread cost measured live ≈ $18 on a $310-max-profit condor ≈ 6% —
  inside the survivable band on its own.

One-to-two data points, so this is suggestive, not proof — but every real
observation so far points to real credits being **at or above** model, i.e. the
haircut scenario may run the wrong direction.

## Verdict + what decides it

1. **Fragility is real:** treat the +$14.7k backtest number as an upper-bound
   scenario, not an expectation. Small pricing bias swings it hugely.
2. **The decisive dataset is real fill-vs-mark slippage**, which the copilot
   already records (`bot_mark` vs `entry_price` per live trade). After ~10 live
   fills we'll know the empirical bias sign and size — revisit this table then
   and read off the row we actually live on.
3. Until then: no change to the live strategy (evidence so far is favorable),
   but no scaling up on the strength of backtest P&L alone.
