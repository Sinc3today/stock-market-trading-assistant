# Structure comparison — condor vs narrow condor vs butterfly

**Question (user, 2026-06-29):** iron condors are credits, so RH ties up the max
loss as collateral. Can we free up capital with narrower wings or a debit
butterfly?

**Method:** `backtests/structure_comparison.py`. Same regime-gated entry days the
bot actually trades (CHOPPY_LOW_VOL / CHOPPY_TRANSITION, 385 days over 5yr), same
BS pricing (live-parity `bs_price`), same frictions (slippage + commission) and
the same 70%-profit-target / 21-DTE management for all three. Only the structure
differs. Shorts 2.5% OTM in every case; the butterfly is a long call fly spanning
the condor's shorts (same ±2.5% win zone, peak at center). Capital = the
collateral RH holds (condor: width − credit; butterfly: the debit).

## Results (5 years, 385 entries)

| structure | win% | total P&L | avg/trade | capital/trade | return on capital |
|---|---|---|---|---|---|
| **condor 2% wings (as-is)** | **66.8%** | **$14,765** | **$38.35** | $502 | **7.6%** |
| condor 1% wings (narrow) | 60.8% | $3,660 | $9.51 | $222 | 4.3% |
| long butterfly (debit) | 61.0% | $5,285 | $13.73 | $238 | 5.8% |

## Conclusion

**Yes, you can free ~half the capital — but the as-is condor is the most capital-
EFFICIENT, not just the highest absolute P&L.** The full-wing condor earns the
best return *per dollar of collateral* (7.6%), so the capital isn't wasted — it's
earning the best return in the book.

- **Narrowing the wings is the worst move.** It cuts capital ~56% ($502→$222) but
  cuts P&L ~75% and drops return-on-capital to 4.3%. You lose far more edge than
  capital.
- **The butterfly is the better low-capital choice.** For ~half the capital
  ($238) it beats the narrow condor on P&L, win-rate, and ROC — but still trails
  the full condor on every metric except capital.

**Practical read:** if the goal is *maximum profit*, keep the full condor. If the
goal is strictly *less buying power per trade* (to run more positions, or hold
cash), the butterfly is the right structure to switch to — not narrower condor
wings — at a cost of ~25% lower return on capital and a lower win-rate.

## Caveats

- **Model-priced** (BS off SPY + VIX). Treat absolutes as approximate; the
  RELATIVE ranking is the signal. The condor's 66.8% here is a touch below the
  canonical 74.1% (different management/pricing details), so the model runs a bit
  conservative.
- **Butterfly fills are harder in reality** — three strikes, wider bid/ask to
  cross than a condor. Real-world butterfly numbers are likely a bit WORSE than
  shown; the condor's edge over it is probably understated here.
- Not walk-forward / not a deployment decision — a capital-efficiency study to
  answer the user's question with numbers, not a signal to change the live book.
