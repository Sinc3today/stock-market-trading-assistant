# Weekend gaps + late-day 1DTE, conditioned (2018-present, incl. Volmageddon + COVID)

**User pushback (2026-07-10):** "average weekend gap ≈ 0" is an unconditional
mean — condition on regime/vol/recency before trusting Friday entries; same
skepticism for late-day 1DTE. **Verdict: the skepticism was right.**

## A. Fri-close → Mon-open gap, conditioned (401 weekends)
| Friday condition | P(gap < −0.8%) | P(any ±0.8% breach) | worst |
|---|---|---|---|
| VIX < 15 | 2.9% | 3.8% | −2.2% |
| VIX 15–20 | 7.3% | 14.0% | −3.1% |
| VIX 20–30 | 15.1% | 31.1% | −4.0% |
| VIX > 30 | 21.4% | **53.6%** | **−10.5%** |
| Friday fell > 1% | 22.2% | 47.6% | −7.5% |
| below 50-day MA | 15.9% | 37.2% | −10.5% |
| (era cut: stable across 2018-20 / 21-23 / 24+ — not a recency artifact) |

Calm-tape weekends are genuinely benign; stressed-tape weekends are 3–7× more
dangerous. **The frozen condor gate (calm VIX) already keeps validated entries
in the benign buckets** — an independent consistency check of the regime gate.

## B. 1DTE entered at the close — breach source by VIX
| VIX | breach AT OPEN (unactionable) | breach intraday (watchdog can act) |
|---|---|---|
| <15 | 3.1% | 23.9% |
| 15–20 | 3.9% | 26.3% |
| >30 | **15.1%** | 38.8% |

In gated calm regimes the overnight gap is minor; in high vol a late-day 1DTE
is a gap lottery. The user's instinct holds exactly where the gates already say
"don't trade."

## Incorporated (informational, never a gate)
The Friday approve-alert tag is now CONDITIONAL: calm tape → "weekend theta ✓
(best slot)"; VIX>20 or a >1% down Friday → "⚠ stressed tape — weekend gap risk
3-7× normal; consider skipping or small size". Context fetched best-effort at
alert time; tag omitted if data unavailable.
