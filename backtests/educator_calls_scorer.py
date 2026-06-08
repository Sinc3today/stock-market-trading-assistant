"""backtests/educator_calls_scorer.py -- score educators' dated forward CALLS
against our own price history, and surface where their read disagreed with the
market (or with us).

The transcript miner (tools/transcript_miner.py) tags each curated video with
its recording date and extracts pipe-delimited CALL lines:

    CALL | instrument | direction | horizon | level-or-trigger | reasoning

This module is the PAYOFF of mining: instead of asking "did they hand us a
rule," it asks "did what they PREDICTED actually happen?" — judged against the
SPY/QQQ/VIX/etc. history we already hold. A verified prediction is worth more
than a static rule: it tells us which discretionary reads have edge, and (when
they were right and our technical system was neutral) where our blind spots are.

Honest framing: this scores DIRECTIONAL hit/miss over a horizon window — a
coarse, first-pass judge, not a P&L sim. It is a lens for finding leads worth a
real walk-forward study, not a verdict on its own. Research only.

Run: python -m backtests.educator_calls_scorer
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

# horizon phrase -> forward trading-day window
_HORIZONS = {"today": 1, "intraday": 1, "days": 5, "1-2wk": 10, "week": 5,
             "weeks": 15, "month": 22, "months": 30}

# direction buckets
_BULL = ("up", "risk-on", "risk on", "bull", "long", "higher", "rally", "breakout")
_BEAR = ("down", "risk-off", "risk off", "bear", "short", "lower", "selloff", "breakdown")
_RANGE = ("range", "flat", "sideways", "chop", "consolidat")
_VOL = ("volatile", "volatility", "vol expansion", "spike")

# the bar move (in %) beyond which a directional call counts as confirmed
_DIR_THRESHOLD = 0.5


def horizon_to_days(horizon: str) -> int:
    h = (horizon or "").strip().lower()
    # match longer keys first so "weeks" doesn't get caught by "week"
    for key in sorted(_HORIZONS, key=len, reverse=True):
        if key in h:
            return _HORIZONS[key]
    return 5            # default: one trading week


def _norm_dir(direction: str) -> str:
    d = (direction or "").strip().lower()
    if any(k in d for k in _BULL):  return "bull"
    if any(k in d for k in _BEAR):  return "bear"
    if any(k in d for k in _RANGE): return "range"
    if any(k in d for k in _VOL):   return "volatile"
    return "unknown"


def parse_kb(text: str) -> list[dict]:
    """Parse the miner's KB markdown into a flat list of call records.
    Each `## title` block carries `<!--vid:ID date:YYYY-MM-DD-->` and zero or
    more `CALL | instrument | direction | horizon | level | reasoning` lines."""
    calls: list[dict] = []
    blocks = re.split(r"\n##\s+", "\n" + text)
    for blk in blocks:
        if not blk.strip():
            continue
        title = blk.splitlines()[0].strip()
        m = re.search(r"<!--vid:(\S+)\s+date:(\S+?)-->", blk)
        vid = m.group(1) if m else None
        date = m.group(2) if m and m.group(2) != "?" else None
        for ln in blk.splitlines():
            ln = ln.strip()
            if not ln.upper().startswith("CALL |"):
                continue
            parts = [p.strip() for p in ln.split("|")]
            # CALL | instrument | direction | horizon | level | reasoning
            if len(parts) < 4:
                continue
            inst = parts[1].upper()
            # skip template/placeholder echoes (small models repeat the schema line)
            if (not inst or inst in ("INSTRUMENT", "<INSTRUMENT>")
                    or any(c in parts[1] for c in "(<>/")):
                continue
            calls.append({
                "title": title, "vid": vid, "date": date,
                "instrument": inst,
                "direction": parts[2],
                "horizon": parts[3],
                "level": parts[4] if len(parts) > 4 else "-",
                "reasoning": parts[5] if len(parts) > 5 else "",
            })
    return calls


def score_call(call: dict, price_df: pd.DataFrame) -> dict:
    """Judge one call against the instrument's history. Verdict ∈
    hit / miss / flat / unscored. `flat` = directional call but the market
    barely moved (|fwd_ret| < threshold). `unscored` = no date / out of range /
    direction we don't grade."""
    out = {**call, "fwd_ret": None, "verdict": "unscored"}
    if not call.get("date"):
        return out
    close = price_df["close"].astype(float)
    try:
        entry_ts = pd.Timestamp(call["date"])
    except Exception:
        return out
    # first bar on/after the call date
    pos = close.index.searchsorted(entry_ts)
    h = horizon_to_days(call["horizon"])
    if pos >= len(close) or pos + h >= len(close):
        return out
    entry, exit_ = close.iloc[pos], close.iloc[pos + h]
    fwd = (exit_ / entry - 1) * 100
    out["fwd_ret"] = round(float(fwd), 3)
    d = _norm_dir(call["direction"])
    if d == "bull":
        out["verdict"] = "hit" if fwd > _DIR_THRESHOLD else ("flat" if fwd > -_DIR_THRESHOLD else "miss")
    elif d == "bear":
        out["verdict"] = "hit" if fwd < -_DIR_THRESHOLD else ("flat" if fwd < _DIR_THRESHOLD else "miss")
    elif d == "range":
        out["verdict"] = "hit" if abs(fwd) < _DIR_THRESHOLD else "miss"
    elif d == "volatile":
        rv = close.iloc[pos + 1: pos + 1 + h].pct_change().std() * 100
        base = close.pct_change().std() * 100
        out["verdict"] = "hit" if rv > base else "miss"
    return out


def aggregate(scored: list[dict]) -> dict:
    graded = [s for s in scored if s["verdict"] != "unscored"]
    hits = sum(1 for s in graded if s["verdict"] == "hit")
    misses = sum(1 for s in graded if s["verdict"] == "miss")
    flats = sum(1 for s in graded if s["verdict"] == "flat")
    n = len(graded)
    return {
        "total": len(scored), "scored": n, "unscored": len(scored) - n,
        "hits": hits, "misses": misses, "flats": flats,
        "hit_rate": round(hits / n * 100, 1) if n else 0.0,
    }


# ── data loading (real CSVs; not exercised by unit tests) ────────────────
def _load(instrument: str) -> pd.DataFrame | None:
    if instrument == "SPY" or instrument == "MARKET":
        from backtests.dipbuy_signal_study import load_spy
        return load_spy()
    path = os.path.join(os.path.dirname(__file__), f"{instrument.lower()}_history.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    if "close" not in df.columns:
        # VIX/yield series store the level in "value" (or a single price column)
        src = "value" if "value" in df.columns else df.columns[-1]
        df = df.rename(columns={src: "close"})
    return df


def main():
    kb = os.path.join(os.path.dirname(__file__), "..", "docs", "kb_youtube_thestockmarket.md")
    if not os.path.exists(kb):
        print(f"No KB yet at {kb}. Run the miner first."); return
    calls = parse_kb(open(kb, encoding="utf-8").read())
    print(f"Educator-calls scorer — {len(calls)} CALLS parsed from the KB\n")
    cache, scored = {}, []
    for c in calls:
        inst = c["instrument"]
        if inst not in cache:
            cache[inst] = _load(inst)
        df = cache[inst]
        scored.append(score_call(c, df) if df is not None else {**c, "verdict": "unscored", "fwd_ret": None})
    # show graded calls
    for s in scored:
        if s["verdict"] == "unscored":
            continue
        print(f"  {s['date']}  {s['instrument']:6} {_norm_dir(s['direction']):8} {s['horizon']:6} "
              f"-> {s['fwd_ret']:+6.2f}%  {s['verdict'].upper():5}  | {s['reasoning'][:40]}")
    agg = aggregate(scored)
    print(f"\nOverall: {agg['hits']} hit / {agg['misses']} miss / {agg['flats']} flat "
          f"of {agg['scored']} scored ({agg['unscored']} unscored) -> hit-rate {agg['hit_rate']}%")
    print("NOTE: coarse directional judge over a horizon window — a lead-finder, not a P&L verdict.")


if __name__ == "__main__":
    main()
