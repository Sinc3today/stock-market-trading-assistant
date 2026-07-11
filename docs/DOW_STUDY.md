# Day-of-week study — the "Friday feeling" tested (5yr SPY)

**User hunch (2026-07-10):** Fridays feel consistently positive; weekends often
give the gains back. Tested on 5 years of daily data + the validated 2-day
condor grouped by entry weekday.

## A. Raw calendar (close→close per weekday)
| day | mean | % positive |
|---|---|---|
| **Mon** | **+0.120%** | **62%** |
| Tue | +0.016% | 49% |
| Wed | +0.111% | 55% |
| Thu | −0.006% | 52% |
| Fri | +0.034% | 53% |

- **Friday is NOT special** — middle of the pack. **Monday is the strong day**
  (and it's intraday strength, not the gap).
- **The weekend doesn't systematically reset gains**: Fri-close→Mon-open gap
  averages −0.001% (54% positive). The *feeling* comes from loss-aversion — the
  rare ugly gap (worst −3.99%) is memorable; the many small positive ones aren't.
- Fri close→Mon CLOSE is actually +0.120% (62% positive) — holding through the
  weekend has been fine on average, tail risk aside.

## B. The actionable part — 2-day condor by ENTRY weekday (pessimistic sim)
| entry | n | win% | avg P&L |
|---|---|---|---|
| **Mon** | 69 | **81.2%** | **$70.84** |
| Tue | 84 | 75.0% | $50.56 |
| Wed | 81 | 66.7% | $25.96 |
| Thu | 73 | 68.5% | $40.64 |
| **Fri** | 78 | **80.8%** | **$65.11** |

**Why Fri/Mon entries win, mechanically:** a Friday-entered 2-day condor collects
THREE calendar days of theta (Sat+Sun+Mon) against ONE day of market movement —
the classic premium-seller's weekend harvest. The user's "Friday feels good"
instinct is real, but the mechanism is **weekend theta on short premium**, not
Friday price drift. Monday entries ride the strongest intraday day.

## Verdict / incorporation (discipline-compatible)
1. **No new gate** — an entry-day filter would be curve-fitting 5 buckets of
   n≈75 each. The Fri/Mon advantage has a mechanical explanation (weekend
   theta), which earns it a soft role, not a hard rule.
2. **User-side sizing heuristic:** when 1-3DTE condor signals fire on a Friday
   or Monday, those are the best-in-class slots to mirror with real money;
   Wednesday signals are the weakest (worst theta-to-exposure ratio).
3. **The real weekend risk** is the rare gap (−4% tail) — already covered by
   defined risk + the 09:15 pre-market gap check + Sunday audit.
4. Approve alerts on Friday condor entries could carry a "weekend theta" note
   (informational only).
