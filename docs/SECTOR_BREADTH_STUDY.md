# Sector-Breadth-as-Risk-Signal Study — breadth is a VOL gauge + dip-timer, NOT a directional avoid

**Date:** 2026-06-08 · **Module:** `backtests/sector_breadth_study.py` · Research only.
**Lead source:** the YouTube/Substack mining thread surfaced "market-breadth deterioration as
a risk signal" as the one formalizable, backtestable claim in the discretionary-macro content.
We test it directly and falsifiably rather than trusting the narrative.

**Breadth proxy:** each day, the % of SPDR sector ETFs trading above their own 50-day MA (the
classic participation gauge). The denominator adapts to whichever sectors have a valid MA that
day (XLRE listed 2015, XLC 2018), so early years use the 9 original sectors and later years all
11. 11 sectors, SPY 2010-01..2026-06.

**Distribution of 50d-breadth:** median **73%**, p25 **44%**, p10 (washout) **11%**.

## Result — SPY forward return & realized vol, conditional on breadth

| Condition | h | n | SPY fwd (baseline) | dir | fwd vol (base) | % up |
|---|---:|---:|---|---|---|---:|
| **LOW** breadth (<p25 = 44%) | 5 | 993 | +0.47% (+0.26%) | **BETTER** | 1.35 (0.87) **↑** | 62% |
| | 10 | 993 | +0.97% (+0.51%) | **BETTER** | 1.33 (0.90) **↑** | 64% |
| **WASHOUT** (<p10 = 11%) | 5 | 345 | +0.93% (+0.26%) | **BETTER** | 1.77 (0.87) **↑** | 64% |
| | 10 | 345 | +1.75% (+0.51%) | **BETTER** | 1.70 (0.90) **↑** | 70% |
| **FALLING** (−15pts/10d & <median) | 5 | 903 | +0.37% (+0.26%) | better | 1.18 (0.87) ↑ | 62% |
| | 10 | 903 | +0.61% (+0.51%) | better | 1.16 (0.90) ↑ | 61% |
| **HIGH** breadth (>median 73%) | 5 | 1970 | +0.09% (+0.26%) | **WORSE** | 0.66 (0.87) **↓** | 59% |
| | 10 | 1966 | +0.24% (+0.51%) | **WORSE** | 0.73 (0.90) **↓** | 63% |

## Interpretation — a split decision

1. **DIRECTION: the narrative is FALSIFIED.** "Breadth deterioration = weakness, get out" is
   wrong for SPY. Low breadth → forward returns **above** baseline; the deeper the washout the
   **harder** the bounce (washout +1.75% vs low +0.97% at 10d, 70% up). This is **buy-the-dip
   confirmed through a fully independent lens** — sector *participation* instead of RSI/price/VIX.
   That makes it the ~11th independent confirmation of the one proven thesis: **SPY is a long
   mean-reversion instrument; deeper dips bounce harder.** High breadth (euphoria) → forward
   returns *below* baseline (buying strength underperforms). Same asymmetry, again.

2. **RISK: the signal is REAL — for condors, not for longs.** Low/falling/washed-out breadth
   carries **~1.5–2× baseline forward realized vol** (washout 1.77 vs 0.87). For premium-selling
   (iron condors) that is exactly breach risk: a breadth-vol gauge would stand condors down
   *before* vol expansions. High breadth = calm tape (vol 0.66 vs 0.87) = the condor-friendly
   environment. So breadth maps cleanly to **the two live edges with opposite signs:**
   - **Condor:** low/falling breadth = RISK-OFF (vol↑ → breach risk → stand down).
   - **Dip-buy:** low/washout breadth = GREEN LIGHT (better entries, bigger bounces).

## Conclusion
**Breadth is a volatility/regime gauge + a dip-timing confirmer — not a directional risk-off
trigger.** It does not tell longs to flee; it tells *condors* to flee and *dip-buys* to lean in.
The result is internally consistent with everything proven (scary-but-rewarded = the signature
of a dip).

**Honest status — in-sample, NOT yet actionable:**
- 2010-2026 is bull-heavy; baseline drift is positive. The **edge is the excess over baseline**,
  which is consistent across all four bands and both horizons — but it's a backtest, not WF.
- The low-breadth vol↑ is partly mechanical (low breadth coincides with recent selloffs, and vol
  clusters). The non-obvious, falsifiable part is the **forward-return asymmetry** (low→better,
  high→worse), which survives.
- Not net of option costs / spread structure. **No source or threshold changed here.**

**Warranted next step (one knob, WF-gated):** test a **breadth-vol gate for the calm condor** —
does standing down premium-selling when 50d-breadth is low/falling improve the condor's
out-of-sample tail (fewer breach losses) without killing too many winners? And a **breadth-washout
confirmer** for the live dip-buy (does requiring washed-out breadth raise dip-buy hit-rate OOS?).
Both are single causal filters, to be walk-forward validated before any wiring — same discipline
as the HYG filter (which failed) and meta-labeling (which failed). Breadth is the most promising
single regime helper tested so far because it splits cleanly by edge instead of being a
kitchen-sink predictor.

---

## Walk-forward verdict (2026-06-08) — neither gate survives; breadth is REDUNDANT

Both hypotheses were built (`backtests/condor_breadth_gate_wf.py`,
`backtests/dipbuy_breadth_confirm_wf.py`) and walk-forward tested. **Both fail**, and for the
same root reason: as an *added* gate on an *already-selected* subset, breadth carries no
orthogonal edge — the existing gate already captures it.

**H1 — breadth gate on the calm condor: FALSIFIED.** 385 tradeable CHOPPY_LOW_VOL days, IS/OOS
60/40. Baseline OOS: 75.3% win, +$84/trade, edge +0.661, breach 3.2%. A breadth floor (≥64%) and
a "not-falling" gate both *lowered* OOS win (−3.8pp, −2.2pp) and edge (−0.11, −0.10) and nudged
breach *up*, while discarding 16–23% of days. The tell: breadth↔VIX correlation on condor days is
−0.39, and the VIX<18 calm gate already removes the high-vol/breach-prone days — by the time a day
is "calm," residual breadth variation no longer predicts breaches. **Don't gate the condor on
breadth.**

**H2 — breadth-washout confirmer on the oversold dip-buy: FALSIFIED (redundant).** 34 oversold
(RSI<30) triggers. The `+low (≤44%)` variant is **identical** to baseline — *every* oversold
trigger already sits on a below-median-breadth day, so "low breadth" adds literally nothing
(RSI<30 and low breadth are the same event). The `+washout (≤11%)` variant nudges OOS sharpe
(0.63→0.71) and pos-years (80%→100%) but cuts triggers 34→23 and **fails the sample-size gate** —
too thin to act on. A faint hint that the *deepest* washouts are the most consistent dips, but not
actionable.

**Conclusion:** breadth deterioration is a real phenomenon (the study stands), but it is **not a
tradeable gate** for either live edge — redundant with VIX (condor) and RSI (dip-buy). This joins
the HYG filter and meta-labeling: a macro/breadth overlay that looks predictive at the full-sample
level but adds no discriminating power once the primary signal has already fired. No source or
threshold changed; breadth stays a dashboard/context gauge (`signals/sector_breadth.py`), not a
gate. Vindicates "the existing gates already capture what the overlay sees."
