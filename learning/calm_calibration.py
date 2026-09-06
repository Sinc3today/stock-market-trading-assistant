"""learning/calm_calibration.py -- is "calm" a true label?

The August 2026 damage did not come from an untested regime. It came from one
we had tested and labelled CALM: VIX stayed under 18 while SPY ran +5.7% in
four days and drove through the live condors' short calls. Implied vol was
low. Realised vol was not. The classifier was not missing a state — it was
mislabelling a state it already knew.

So this instrument scores the claim the bot makes EVERY DAY:

    "VIX < 18 and ADX < 32 means the next 5 sessions stay inside the band
     we are selling."

For each calm-labelled day it records the move VIX *implied* and, five
sessions later, the move that actually happened. The ratio of the two is the
whole measurement.

Why this and not a high-vol shadow recorder (docs/VALIDATION_AGENDA.md item 9):
the event rate is ~100% of days instead of ~5%, so it converges in weeks
rather than a year; it measures the failure that actually cost money; and it
needs no counterfactual trade — it only scores a claim we already make.

A breach is judged against the SHORT STRIKE (~0.85 sigma at 0.20 delta), not
1 sigma, because that is the move that actually hurts a condor.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

# Verdicts
CALIBRATED = "calibrated"
UNDERPRICED = "underpriced"
OVERPRICED = "overpriced"
NO_DATA = "no_data"

HORIZON_DAYS = 5           # sessions ahead we score
TRADING_DAYS_PER_YEAR = 252
# 0.20-delta shorts sit near 0.85 standard deviations out. A move past THAT is
# what threatens the position — 1 sigma would understate how often we are hurt.
SHORT_STRIKE_SIGMAS = 0.85
# Median realised/implied ratio bands for the verdict.
UNDERPRICED_ABOVE = 0.90
OVERPRICED_BELOW = 0.45
MIN_SAMPLE = 5


def implied_move_pct(vix: float | None, days: int = HORIZON_DAYS) -> float:
    """The move VIX implies over `days` sessions, as a percent of spot.

    VIX is an annualised 1-sigma vol, so scale by sqrt(days / 252).
    """
    try:
        v = float(vix)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0 or days <= 0:
        return 0.0
    return v * math.sqrt(days / TRADING_DAYS_PER_YEAR)


def score_day(vix: float | None, actual_move_pct: float | None,
              days: int = HORIZON_DAYS) -> dict | None:
    """Compare one day's implied band against what actually happened."""
    if vix is None or actual_move_pct is None:
        return None
    implied = implied_move_pct(vix, days)
    if implied <= 0:
        return None
    actual = abs(float(actual_move_pct))
    threshold = implied * SHORT_STRIKE_SIGMAS
    return {
        "implied_move_pct": round(implied, 3),
        "actual_move_pct": round(actual, 3),
        "ratio": round(actual / implied, 3),
        "short_strike_pct": round(threshold, 3),
        "breached": bool(actual > threshold),
    }


def summarise(rows: list[dict | None]) -> dict:
    """Aggregate scored days into a calibration verdict."""
    clean = [r for r in rows if r]
    if not clean:
        return {"n": 0, "breaches": 0, "breach_pct": 0.0, "median_ratio": 0.0,
                "mean_ratio": 0.0, "verdict": NO_DATA}
    ratios = [r["ratio"] for r in clean]
    breaches = sum(1 for r in clean if r["breached"])
    median = statistics.median(ratios)
    verdict = NO_DATA
    if len(clean) >= MIN_SAMPLE:
        if median > UNDERPRICED_ABOVE:
            verdict = UNDERPRICED
        elif median < OVERPRICED_BELOW:
            verdict = OVERPRICED
        else:
            verdict = CALIBRATED
    return {
        "n": len(clean),
        "breaches": breaches,
        "breach_pct": round(breaches / len(clean) * 100, 1),
        "median_ratio": round(median, 3),
        "mean_ratio": round(sum(ratios) / len(ratios), 3),
        "verdict": verdict,
    }


# ── persistence ──────────────────────────────────────────────────

def _path() -> str:
    d = os.path.join(config.LOG_DIR, "learning")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "calm_calibration.jsonl")


def load_all() -> list[dict]:
    try:
        with open(_path()) as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except Exception:
        return []


def _rewrite(rows: list[dict]) -> None:
    tmp = _path() + ".tmp"
    with open(tmp, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, _path())


def log_calm_day(date_iso: str, *, regime: str, vix: float, adx: float,
                 spot: float) -> bool:
    """Record today's calm claim. Idempotent per date."""
    rows = load_all()
    if any(r.get("date") == date_iso for r in rows):
        return False
    rows.append({
        "date": date_iso, "regime": regime, "vix": vix, "adx": adx,
        "entry_spot": spot,
        "implied_move_pct": round(implied_move_pct(vix), 3),
        "horizon_days": HORIZON_DAYS,
        "resolved": False, "actual_close": None, "actual_move_pct": None,
        "ratio": None, "breached": None,
    })
    _rewrite(rows)
    logger.info(f"calm_calibration: logged {date_iso} "
                f"(VIX {vix}, implied {implied_move_pct(vix):.2f}%)")
    return True


def unresolved() -> list[dict]:
    return [r for r in load_all() if not r.get("resolved")]


def resolve_day(date_iso: str, actual_close: float) -> bool:
    """Score a logged day against what the market actually did."""
    rows = load_all()
    for r in rows:
        if r.get("date") != date_iso or r.get("resolved"):
            continue
        entry = r.get("entry_spot")
        if not entry:
            return False
        move = (float(actual_close) - float(entry)) / float(entry) * 100
        scored = score_day(r.get("vix"), move)
        r["resolved"] = True
        r["actual_close"] = actual_close
        r["actual_move_pct"] = round(move, 3)
        if scored:
            r["ratio"] = scored["ratio"]
            r["breached"] = scored["breached"]
            r["short_strike_pct"] = scored["short_strike_pct"]
        _rewrite(rows)
        logger.info(f"calm_calibration: resolved {date_iso} move {move:+.2f}% "
                    f"ratio {r.get('ratio')} breached={r.get('breached')}")
        return True
    return False


def calibration() -> dict:
    """Verdict over every resolved day."""
    rows = [r for r in load_all() if r.get("resolved") and r.get("ratio") is not None]
    return summarise([{"ratio": r["ratio"], "breached": r.get("breached", False),
                       "implied_move_pct": r.get("implied_move_pct"),
                       "actual_move_pct": r.get("actual_move_pct"),
                       "short_strike_pct": r.get("short_strike_pct")}
                      for r in rows])
