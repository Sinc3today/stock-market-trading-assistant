# QQQ condor transfer study — verdict: DO NOT ADD

**Question (user, 2026-07-09):** the dip-buy transferred to QQQ — does the calm-
regime iron condor too?

**Method:** `backtests/qqq_condor_transfer.py`. Deploy-realistic: the SAME
385 CHOPPY_LOW_VOL/TRANSITION entry days the live SPY/VIX gate picks (market
regime is market-wide), same structure/management, but QQQ condors priced with
^VXN (Nasdaq vol — pricing QQQ options off VIX would overstate the edge).

| same entry days | win% | total | avg | capital | ROC | per-year avg |
|---|---|---|---|---|---|---|
| SPY (baseline) | 66.8% | +$14,765 | $38.35 | $502 | 7.6% | +17/+1/+29/+77/+35 |
| QQQ (VXN) | 60.8% | +$5,988 | $15.55 | $361 | 4.3% | **−8/−30**/+28/+41/+17 |

**Read:**
- QQQ condors are profitable in aggregate but strictly WORSE than SPY condors on
  the same days — lower win rate, ~40% of the P&L, lower return-on-capital —
  and NEGATIVE in 2 of 5 years (fails the positive-year-fraction gate every
  other accepted strategy passes).
- Mechanism: a market-wide (SPY/VIX) "calm" call doesn't mean QQQ is range-bound
  — tech momentum runs through QQQ's short calls inside SPY-calm regimes. Also
  strictly dominated on capital-efficiency by the SPY butterfly (5.8% ROC at
  ~$240/trade) for anyone capital-constrained.
- Consistent with the project's directional map: **SPY is the range/condor
  instrument; QQQ is the mean-reversion/dip-buy instrument.** Different edges
  for different underlyings — not one strategy sprayed everywhere.

**Future work (LOW priority, un-tested):** classify QQQ's own regime with
QQQ-ADX + VXN-calibrated thresholds. Deliberately NOT pursued now — it means new
tunable knobs, and the threshold-WF study says fine-tuning gates is noise-chasing.
