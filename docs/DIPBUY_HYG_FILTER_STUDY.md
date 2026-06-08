# Dip-Buy HYG Risk-Off Filter — Study (negative)

**Date:** 2026-06-07 · **Module:** `backtests/dipbuy_hyg_filter.py` · Research only.

## Hypothesis

The oversold dip-buy's worst trades are falling-knife dips (e.g., 2020 COVID). Credit
(HYG) leads equities into risk-off, so: **SPY oversold + credit OK = a buyable bounce;
SPY oversold + credit blowing out = a real cascade, skip it.** A HYG risk-off filter should
remove the losers and improve the edge.

## Result — FALSIFIED

**1. "HYG below its 50d MA" flags ALL 34 triggers** — useless. When SPY is oversold (RSI<30)
it's in a selloff, so credit is almost always soft too; the condition is correlated with the
trigger by construction (filtered book = 0 trades).

**2. Credit-stress *depth* does not discriminate winners from losers.** Correlation of HYG
drawdown (% below 50d MA) vs trade P&L = **+0.12** (≈ zero, and the wrong sign for the
thesis). HYG 20-day return vs P&L = +0.06.

- The **deepest** credit-stress dips were **winners**: 2020-03-23 (HYG −19% vs MA) +$214;
  2020-03-12 (−11%) +$127. The most violent COVID panic bounced hardest.
- Losers are scattered across all stress levels: 2020-03-09 (−7%, −$309), 2020-02-25 (−1.4%,
  −$278), 2018-10-10 (−1.6%, −$261).

## Conclusion

**Do not add the HYG filter** — it doesn't separate good dips from bad, and would remove
winners as often as losers. The dip-buy's protection is structural: the **defined-risk bull
debit spread** caps the worst case (bounded at −$309 here), which is the right way to handle
falling-knife risk for a mean-reversion bounce. The live dip-buy is left unchanged.

Broader takeaway (vindicates the "too strict/messy" worry): for a short-horizon
mean-reversion bounce, a macro credit overlay adds complexity without edge. The yield-curve
helper is untested but lower-priority — if credit (the more equity-leading signal) shows
nothing, a slower macro tell is unlikely to discriminate these days. Defined risk > macro
overlay here.
