# Magnet study — do "magnet zones" exist, and do they pay?

**Question (user, 2026-07-15):** the intuition of "trading away from magnet
zones" — is price pulled toward certain levels, and should condors be centered
on the magnet?

**Data honesty first:** the classic options magnets (max pain, OI walls, GEX
flip) need HISTORICAL open interest, which no source we have sells
retroactively. Untestable today. Fix shipped with this study: a daily wall
snapshot job (10:05 ET → `logs/walls_history.jsonl`, `learning/walls_logger`)
so the real options-magnet study is possible after ~3-6 months of accumulation.

What we could test with 8.5 years of price data
(`backtests/magnet_study.py`):

## A. Is the 20-day mean a magnet? — WEAK, chop-only, not tradeable as a pull

5-session forward return after price deviates from MA20:

| regime | deviation | n | fwd 5d ret | reverts toward MA20 |
|---|---|---|---|---|
| choppy calm | +0.5..1.5% | 202 | +0.10% | 40% |
| choppy calm | >+1.5% | 216 | **−0.02%** | 44% |
| trending calm | +0.5..1.5% | 136 | +0.12% | 36% |
| trending calm | >+1.5% | 270 | **+0.30%** | 34% |

In **chop**, stretching above the mean kills forward drift (+0.35% baseline →
−0.02% when stretched) — the magnet acts as a *drift damper*, but the revert
rate stays under 50%, so there is no reliable snap-back to trade directionally.
In a **trend**, the magnet doesn't hold at all (+0.30% even stretched —
momentum wins). (The "near magnet 100% reverts" rows in the raw output are
definitionally true and excluded here.)

## B. The surprise: stretched-from-mean chop days are BETTER condor entries

7DTE condor (ladder machinery) in choppy_low_vol, bucketed by |spot−MA20|:

| entry distance from MA20 | n | win% | avg |
|---|---|---|---|
| on magnet (<0.5%) | 77 | 81% | $37.73 |
| 0.5–1.5% | 225 | 81% | $39.21 |
| **>1.5% stretched** | **234** | **92%** | **$53.73** |

Counter to the intuition ("enter at the magnet") — and it makes sense with
Part A: a stretched chop-day has already *spent* its directional energy
(forward drift ≈ 0), so a condor centered on the stretched spot faces less
continuation risk. **Status: single-pass finding, no OOS split yet — a sizing
TILT candidate, never a gate.** Needs an era-split confirmation pass before it
touches even an alert tag.

## C. OPEX pinning — NOT VISIBLE in our data

- Weekly high-low range: OPEX weeks 3.11% vs ordinary weeks 3.18% (n=95/339)
  — no compression.
- 7DTE condors entered OPEX Monday: 81% / $29.95 (n=21) vs other Mondays
  80% / $34.08 — no edge, small n.

The dealer-pinning story may still be true at the strike level (that's the OI
data we lack), but at the index-range level it does not show up.

## Verdicts

1. **No new gates.** The condor already harvests the only magnet that shows
   up (chop's drift-damping) without needing to know where the magnet is.
2. **Part B is the one lead**: stretched-entry condors in chop looked
   meaningfully better — flag for an OOS confirmation pass before using even
   as an alert tag.
3. **OPEX pinning: falsified** at the range level. Don't trade OPEX week
   differently on pinning grounds.
4. **The real options-magnet study starts today**, via the forward wall log.
