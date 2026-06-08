# 50d-Low Breakdown as a 2nd Dip-Buy Trigger (positive)

**Date:** 2026-06-07 · **Module:** `backtests/dipbuy_breakdown_study.py` · Research only.

## Result — a legitimate complementary trigger

| Trigger | n | mean/trade | win% | total |
|---|---:|---:|---:|---:|
| 50d-low breakdown (Donchian down) | 80 | +$70 | 64% | +$5,610 |
| oversold (RSI<30) | 34 | +$135 | 68% | +$4,600 |
| **union (either)** | 100 | +$90 | 65% | **+$9,049** |

- **Complementary, not redundant:** of 80 breakdown triggers, only 14 fire same-day as RSI<30
  and 36 within 3 days — so ~44 are genuinely different dips. The union nearly **triples the
  opportunity set** (34 → 100) at positive expectancy.
- **More consistent than RSI<30:** positive in **13 of 15 years** (only 2020 −$86, 2026 −$603/n4).
  Lower per-trade edge ($70 vs $135) — a broader net catches more marginal dips — but solid.

## Conclusion

A valid **second buy-weakness trigger** for the dip-buy. Recommended action: add it to the
live dip-buy **forward-test** (fire on RSI<30 OR 50d-low breakdown), which both validates it
OOS and ~triples the forward-test frequency (faster evidence). Same discipline as the dip-buy:
paper/candidate book, defined-risk debit, no real money until forward-confirmed. Caveats:
in-sample, modeled BS pricing, mostly-bull tape (breakdowns continue down in a real bear —
the defined-risk structure is the protection).

## Reinforces the core thesis

Yet another confirmation: **SPY is a buy-the-dip instrument.** Both independent weakness
signals (oversold RSI, new-low breakdown) bounce; momentum and shorts don't work.
