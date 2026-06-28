"""tools/rescore_predictions.py -- one-off: re-score historical predictions.

The old daily predictions mirrored the strategy and scored neutral on a broken
0.25% band. This reconstructs each resolved prediction under the new methodology:
the INDEPENDENT directional forecast (price+VIX as of that date) + the VIX-implied
band, then re-scores against the move that actually happened. Original direction
is preserved as `direction_original` and entries are tagged `rescored: True` for
transparency — this is a corrected reconstruction, not a rewrite of live calls.

Run from repo root:  .venv/bin/python -m tools.rescore_predictions
"""
import json
import os

import pandas as pd

from signals.directional_forecast import forecast_direction
from learning.outcome_resolver import OutcomeResolver, NEUTRAL_TOLERANCE_PCT
from atomic_io import atomic_write_text

PRED_PATH = "logs/learning/predictions.jsonl"


def _spy_df():
    df = pd.read_csv("backtests/spy_history.csv", index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index).date
    return df.sort_index()


def _vix_lookup():
    try:
        from data.vix_client import VIXClient
        vdf = VIXClient().get_history(days=400)
        if vdf is not None and len(vdf):
            return {pd.to_datetime(d).date(): float(c) for d, c in vdf["close"].items()}
    except Exception:
        pass
    return {}


def main():
    rows = [json.loads(l) for l in open(PRED_PATH) if l.strip()]
    spy = _spy_df()
    vix = _vix_lookup()

    before = {"correct": 0, "wrong": 0}
    after = {"correct": 0, "wrong": 0}
    changed = 0

    for r in rows:
        if r.get("outcome") in ("correct", "wrong"):
            before[r["outcome"]] += 1
        entry = r.get("entry_spy")
        close = r.get("actual_close")
        d = pd.to_datetime(r["date"]).date()
        if entry is None or close is None or not r.get("resolved"):
            continue
        asof = spy[spy.index <= d]
        if len(asof) < 210:
            continue
        f = forecast_direction(asof, vix.get(d))
        new_dir = f["direction"]
        band = f["expected_move_pct"] or NEUTRAL_TOLERANCE_PCT
        new_outcome = OutcomeResolver._score(new_dir, entry, close, neutral_band=band)

        if "direction_original" not in r:
            r["direction_original"] = r.get("direction")
        if r.get("outcome") != new_outcome or r.get("direction") != new_dir:
            changed += 1
        r["direction"] = new_dir
        r["expected_move_pct"] = band
        r["reasons"] = f["reasons"]
        r["outcome"] = new_outcome
        r["rescored"] = True
        if new_outcome in ("correct", "wrong"):
            after[new_outcome] += 1

    atomic_write_text(PRED_PATH, "".join(json.dumps(r) + "\n" for r in rows))

    def acc(d):
        t = d["correct"] + d["wrong"]
        return f"{d['correct']}/{t} = {100*d['correct']/t:.1f}%" if t else "n/a"

    print(f"records: {len(rows)} | changed: {changed}")
    print(f"BEFORE accuracy: {acc(before)}")
    print(f"AFTER  accuracy: {acc(after)}")


if __name__ == "__main__":
    main()
