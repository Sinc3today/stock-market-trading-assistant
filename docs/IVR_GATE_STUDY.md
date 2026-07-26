# IV-Rank condor gate study — the gate is backwards

**Question (2026-07-26):** the bot stood down on every choppy-low-vol day for
~two weeks. Cause: `OptionsLayer` rejects a neutral/condor unless **IV Rank ≥ 50**
(`IV_RANK_HIGH`). Is that gate justified, or is it suppressing profitable trades?

Study: `backtests/ivr_gate_study.py` — 45DTE condor (the live daily-play DTE the
gate governs) on choppy_low_vol days (not-trending & VIX<18), 2018-present, IV
Rank = trailing-252d VIX percentile (the live proxy), OOS era split, 10% haircut.

## Result

| IVR bucket | n | win% | avg | 2018-22 | 2023+ | verdict |
|---|---|---|---|---|---|---|
| **IVR <25** | **470** | **77%** | **+$23.15** | +$16.18 | +$28.31 | **PASS** |
| IVR 25-50 | 32 | 50% | −$52.48 | −$90.95 | +$84.92 | fail-OOS |
| IVR 50-75 | 1 | — | (n<30) | | | |
| IVR 75+ | 0 | — | | | | |

Under the 10% haircut the **IVR<25 bucket still PASSES both eras** (69% win,
+$6.23/trade). The 25-50 bucket stays negative but is tiny (n=32) and era-unstable.

## The structural problem: the gate contradicts its own regime

VIX<18 (the choppy_low_vol condition) **mechanically forces IV Rank low.** With
VIX's trailing-year range of 13.5–31.0, VIX<18 maps to IVR < ~26. So:

- **IVR≥50 and VIX<18 are almost mutually exclusive** — only **1 day in 8 years**
  satisfied both. The gate is a near-permanent block on the condor's home regime.
- The "sell premium only when it's expensive" logic behind IVR≥50 is sound in
  general, but it's **incoherent when combined with a VIX<18 regime filter** —
  you've already required cheap vol, then demanded expensive vol on top.

## What actually has edge

Condors in choppy_low_vol are **just good**: the IVR<25 bucket — which is
essentially *all* of VIX<18 — is **77% win, +$23/trade, positive in both eras,
survives the haircut** (n=470, a big sample). The regime's own VIX<18 condition
already selects the calm, range-bound, high-win-rate environment. The IVR gate
adds nothing but a near-permanent veto.

The 25-50 sliver (VIX right at 17-18, compressed yearly range) looks weak, but
n=32 with a −91/+85 era split is noise, not signal — it sits on the regime
boundary and shouldn't drive the rule.

## Verdict

**The IVR≥50 gate is miscalibrated and should be removed (or dropped to a low
floor) for the neutral/condor path.** It contradicts the VIX<18 regime filter,
blocks 470 validated-profitable trades, and is why the bot sat out its best
regime for weeks. The VIX<18 regime gate already does the vol-selection job.

**Recommended change:** drop the `direction == "neutral"` IVR≥50 requirement in
`OptionsLayer.analyze`. The regime layer (VIX<18 → CHOPPY_LOW_VOL → condor) is
the validated selector; premium quality is already guarded separately by the
`MIN_CREDIT_SPREAD_RR` r/r gate. Keep the r/r gate; remove the IVR veto.

**Caveat to honor at deploy:** today's IVR is ~29 (in the noisy 25-50 sliver),
and VIX 18.6 currently reads choppy_transition anyway — so this fix re-arms the
bot for the *next* clean VIX<18 day, it doesn't manufacture a trade today.
Thresholds are frozen by policy; this study is the re-run that licenses the
change. Nothing deployed yet.
