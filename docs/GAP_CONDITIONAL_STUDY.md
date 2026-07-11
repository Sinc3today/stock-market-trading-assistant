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

## C. Weeknight sentinel extension (user, 2026-07-11) — verdict: Sunday-only stands
Does a 10 PM ET evening ES read reveal the NEXT morning's gap on weeknights too?

| evening read | corr w/ next open gap | direction right | big (>0.5%) gaps caught |
|---|---|---|---|
| Mon → Tue | 0.66 | 68% | 50% |
| Tue → Wed | 0.73 | 71% | 45% |
| Wed → Thu | 0.56 | 71% | 61% |
| Thu → Fri | 0.45 | 70% | 40% |
| **Sun → Mon** | **0.75** | **76%** | **82%** |

**Why Sunday is special:** by 10 PM Sunday, futures have spent 4 hours pricing
TWO DAYS of accumulated news — most of the gap is done forming. On a weeknight,
10 PM is only ~4h into a 17.5h overnight; Asia/Europe/pre-market still move the
number, so an evening read catches only 40–61% of big gaps — enough to breed
false confidence ("sentinel was quiet, must be safe") on the ~half it misses.

**Decision: no weeknight 10 PM sentinel.** Weeknight gap protection stays with
the 09:15 pre-market check, which sees the COMPLETED overnight. Sunday keeps its
10:04 PM sentinel (82% catch, ~11h warning).
