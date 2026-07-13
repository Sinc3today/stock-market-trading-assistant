# Directional-spread study — what makes a TRENDING_UP_CALM entry profitable?

**Question (user, 2026-07-13):** we flipped to `trending_up_calm`. Which
signals/conditions make the regime's directional bull spread a good trade?

**Method:** `backtests/directional_spread_study.py` — regime reconstructed
2018-present with the live rules (Wilder ADX(14) ≥ 32, VIX < 18, close >
200MA → 511 qualifying days out of 2,090). On every qualifying day, both live
structures modeled at 45DTE with the project's r=0 Black-Scholes (sigma =
that day's VIX), managed exactly like live: 70% profit target, close at
21 calendar DTE. One signal bucketed at a time — no combo mining. Headline
candidates re-checked OOS (2018-22 vs 2023+).

**Model honesty:** BS close-marks, no fill haircut — absolute dollars are
optimistic (SKEW_STRESS discipline). The findings below are RELATIVE bucket
comparisons, which survive haircuts.

## Baselines — the regime itself is the edge

| structure | n | win% | avg | total (8.5yr) | worst |
|---|---|---|---|---|---|
| Bull PUT credit (sell 0.40Δ, $5 wing) | 511 | **79%** | $59 | +$30,140 | −$254 |
| Bull CALL debit (buy 0.55Δ / sell 0.30Δ) | 511 | 68% | **$117** | +$59,932 | −$640 |

Both are positive in BOTH eras. Credit = higher win rate, smaller/steadier;
debit = lower win rate, ~2× the dollars and much bigger upside capture.
Neither dominates — it's a temperament choice, not an edge difference.

## Finding 1 — the extension gate is REAL (strongest signal, OOS-confirmed)

Distance above the 200-day MA at entry:

| ext bucket | credit win/avg | debit win/avg |
|---|---|---|
| <5% | 86% / $71 | 68% / $102 |
| 5-7% | 81% / $60 | 71% / $104 |
| **7-9%** | **88% / $79** | **85% / $226** |
| **>9%** | **71% / $46** | **59% / $77** |

OOS split (the honesty check — direction must agree in both eras):

| era | credit ≤9% | credit >9% | debit ≤9% | debit >9% |
|---|---|---|---|---|
| 2018-22 | 79% / $58 | 63% / $32 | 72% / $74 | **43% / $18** |
| 2023+ | 94% / $88 | 74% / $51 | 81% / $258 | 65% / $98 |

**Verdict: chasing a bull spread when SPY is >9% stretched above its 200MA is
the one clearly bad entry in this regime — in the older era the debit spread
was a coin-flip loser (43%).** This independently validates the live
`EXTENDED_TREND_MAX_PCT = 9.0` skip gate in `regime_detector.py` with
directional-spread-specific evidence (it was previously justified by the
regime backtest alone). The 7-9% band — where we sit today (+8.7%) — is
historically the SWEET SPOT, not the danger zone; danger starts past the gate.

## Finding 2 — dip entries are a mild tilt, not a gate

"Dip" = below the 20-day MA or a −0.5%+ 3-day pullback at entry:

| era | credit dip | credit no-dip | debit dip | debit no-dip |
|---|---|---|---|---|
| 2018-22 | 74% / $59 | 74% / $48 | 64% / $75 | 63% / $53 |
| 2023+ | 83% / $83 | 82% / $60 | 77% / $247 | 69% / $134 |

Direction agrees everywhere (dip avg > no-dip avg) but win rates barely move
and 2018-22 magnitudes are small. **Use as a sizing/patience tilt — "a red
day in an uptrend is a slightly better entry than a green one" — never as a
wait-for-it gate** (you'd skip most of a profitable regime waiting).
Consistent with the sector-breadth finding (breadth = dip-buy confirmer).

## Finding 3 — things that DON'T matter (measured, so we stop wondering)

- **Donchian 20-day breakout day:** slightly WORSE than inside-range entries
  (both structures) — buying the exact new-high print is mild chasing.
- **RSI(14):** >70 modestly worse, <50 modestly better — same story as
  Finding 2, weaker. No bucket flips sign.
- **Day of week / up-streak / VIX sub-bucket (within <18):** noise. No
  Fri/Mon effect here (unlike short-DTE condors — different trade, different
  physics: 45 days of theta doesn't care which weekday it started).
- **Trend age >30 days** looked perfect (100%/19) but n=19 — not evidence.

## What this means for live behavior

1. **No code changes from this study.** The one actionable gate (>9%
   extension skip) is already enforced in `regime_detector.py`; this study
   upgrades its evidence from borrowed-heuristic to OOS-validated.
2. Today's tape (+8.7% ext, fresh trend, VIX 15) sits in the historically
   strongest entry band. The 09:45 play firing today is consistent with the
   study.
3. Possible follow-on (needs user sign-off, then its own walk-forward): a
   **dip-tilt tag** on directional-spread approve alerts (like the DOW tag on
   short-DTE condors) — informational sizing hint, never a gate.

**Alert-side (same session):** verified end-to-end that disciplined opens
push the emergency approve alert with RH-shaped legs — the daily 45DTE path
was already wired (`main.py` → `job_paper_broker` → `notifier.approve`); a
regression test now locks in the vertical-spread leg rendering
(lowercase/`expiration`-key journal shape → `BUY $724 PUT` lines + real
expiry). The user never saw one because every directional day so far was
cap- or regime-skipped.
