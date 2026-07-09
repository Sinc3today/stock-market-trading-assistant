# Edge robustness: threshold walk-forward + 2019-2020 crash replay

Audit T2 #7 and #10 (2026-07-09). Companion to SKEW_STRESS.md — together these
answer "how honest is the backtested condor edge?"

## 1. Threshold walk-forward (`backtests/threshold_walkforward.py`)

Rolling 4-fold WF over the (ADX_TREND_MIN, VIX_CALM_MAX) condor gate,
tune-on-fold-k / test-on-fold-k+1, vs the fixed live pair (32, 18):

| fold | IS-best pair | OOS P&L w/ IS-best | OOS P&L w/ live (32,18) |
|---|---|---|---|
| 1 | (36, 20) | −$798 | **+$408** |
| 2 | (26, 18) | −$586 | **+$957** |
| 3 | (36, 18) | +$10,928 | +$9,492 |

**Read:**
- **No fold independently picks (32, 18)** — the exact values can't claim to be
  "the" optimum; IS-best bounces across the whole grid (curve-fit confirmed).
- **But chasing the IS-optimum is WORSE than the fixed pair** in 2 of 3 folds —
  classic overfitting demonstration. The live pair sits in a flat, decent
  neighborhood; the lesson is STOP FINE-TUNING these thresholds (each further
  bump is more likely noise-chasing than improvement), not that they're wrong.
- Fold 3 (most recent slice) carries almost all the P&L — the edge is
  concentrated in recent market conditions. See §2.

## 2. 2019-2020 crash replay (`backtests/crash_replay.py`)

Replayed the CURRENT classifier + condor sim over 2019-2020 (yfinance — outside
our 2021-2026 backtest window):

- **Full 2019-2020: n=125 condors, 48.0% win, −$3,724 total.** The condor
  strategy would have been a NET LOSER over that period. The 66-74% win edge is
  a property of 2021-2026 conditions, not a universal law.
- **Feb 2020 — the failure mode:** the classifier labeled 13 days in Feb 2020
  CHOPPY_LOW_VOL (calm ADX + calm VIX) *as the market topped*, entered condors
  on all of them, and **lost all 13** (−$3,253). "The calm before the crash"
  looks exactly like condor heaven to an ADX+VIX gate.
- From March 2020 on, the gates correctly stood aside (trending/choppy-high-vol
  → no condors) for the rest of the year.
- **Defined risk held:** worst single trade −$314 ≈ max loss per 1-lot. The
  crash produced a bounded drawdown, not a blow-up — and the new concentration
  guard (T1.2) now limits how many of those losses can stack simultaneously.

## Combined verdict

1. The condor edge is **real in-sample but conditional**: it needs 2021-26-like
   conditions, model-accurate (or better) credits, and no calm-top ambush.
2. **Defined risk + concentration guard are the actual safety net** — the gates
   will not see a fast regime transition coming.
3. **Freeze the thresholds.** Further ADX/VIX fine-tuning is noise-chasing (the
   hypothesis engine's whitelist bar was raised for this reason — T3#11).
4. Future work (untested idea, NOT shipped): a term-structure/VIX-slope gate to
   catch the "calm top" pattern Feb-2020 exposed. Needs its own falsification
   pass before any deployment.
