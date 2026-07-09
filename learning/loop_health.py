"""learning/loop_health.py -- learning-loop health monitor + CSV auto-refresh.

The off-hours learner silently returned 0 rows for ~5 weeks and predictions were
mis-scored for weeks — both found only because the user manually looked. This
makes silence impossible: assess_health() turns artifact freshness into a list of
issues (alerted daily), and refresh_spy_history() keeps the replay CSV current so
the most common staleness can't recur.

Pure: assess_health(). I/O: gather_and_assess() reads the artifacts;
refresh_spy_history() appends recent SPY bars (yfinance) atomically.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import date, datetime, timedelta

from loguru import logger

import config
from atomic_io import atomic_write_text

# Freshness thresholds (calendar days). off-hours/KB are weekly-ish; predictions
# are weekday — generous enough to clear a holiday weekend without false alarms.
OFFHOURS_MAX_AGE = 8
PREDICTION_MAX_AGE = 5
KB_MAX_AGE = 8
CSV_MAX_AGE = 5
RH_SYNC_MAX_AGE = 4          # days since last SUCCESSFUL sync (survives weekends)
RH_TOKEN_PATH = os.path.expanduser("~/.tokens/robinhood.pickle")


def assess_health(today: date, *, last_offhours_date, last_prediction_date,
                  last_kb_date, csv_last_date, rh_last_sync_date) -> list[str]:
    """Pure: given each artifact's freshness, return human-readable issues
    (empty = healthy)."""
    issues: list[str] = []

    def stale(d, max_age):
        return d is None or (today - d).days > max_age

    if stale(last_offhours_date, OFFHOURS_MAX_AGE):
        issues.append(f"off-hours learner stale (last output {last_offhours_date or 'never'})")
    if stale(last_prediction_date, PREDICTION_MAX_AGE):
        issues.append(f"no recent daily prediction (last {last_prediction_date or 'never'})")
    if stale(last_kb_date, KB_MAX_AGE):
        issues.append(f"knowledge base not growing (last entry {last_kb_date or 'never'})")
    if stale(csv_last_date, CSV_MAX_AGE):
        issues.append(f"spy_history.csv stale (last bar {csv_last_date or 'missing'})")
    # T1.3: judge the RH session by the last SUCCESSFUL sync, not the pickle
    # file existing — an expired token false-passed the old check for a week.
    if stale(rh_last_sync_date, RH_SYNC_MAX_AGE):
        issues.append("RH sync not succeeding (session expired?) — re-run rh_sync login")
    return issues


# ── disk readers ────────────────────────────────────────────────────────────

def _latest_offhours_date() -> date | None:
    files = glob.glob(os.path.join(config.LOG_DIR, "learning", "off_hours", "*.json"))
    dates = []
    for f in files:
        try:
            dates.append(date.fromisoformat(os.path.basename(f)[:10]))
        except ValueError:
            continue
    return max(dates) if dates else None


def _last_jsonl_date(path: str) -> date | None:
    if not os.path.exists(path):
        return None
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line).get("date")
                if d:
                    last = date.fromisoformat(str(d)[:10])
            except (json.JSONDecodeError, ValueError):
                continue
    return last


def _csv_last_date(csv_path: str) -> date | None:
    if not os.path.exists(csv_path):
        return None
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return pd.to_datetime(df.index).max().date()
    except Exception:
        return None


def gather_and_assess(today: date | None = None,
                      csv_path: str = os.path.join("backtests", "spy_history.csv")) -> list[str]:
    today = today or date.today()
    learn = os.path.join(config.LOG_DIR, "learning")
    from learning.rh_sync import last_success_date
    return assess_health(
        today,
        last_offhours_date=_latest_offhours_date(),
        last_prediction_date=_last_jsonl_date(os.path.join(learn, "predictions.jsonl")),
        last_kb_date=_last_jsonl_date(os.path.join(learn, "knowledge.jsonl")),
        csv_last_date=_csv_last_date(csv_path),
        rh_last_sync_date=last_success_date(),
    )


# ── CSV auto-refresh ────────────────────────────────────────────────────────

def _yf_fetch(start: str):
    """Recent SPY daily bars from yfinance, indexed by date, cols
    open/high/low/close/volume."""
    import pandas as pd
    import yfinance as yf
    h = yf.Ticker("SPY").history(start=start, auto_adjust=True)
    if h is None or not len(h):
        return pd.DataFrame()
    h.index = pd.to_datetime(h.index).tz_localize(None)
    out = h[["Open", "High", "Low", "Close", "Volume"]].copy()
    out.columns = ["open", "high", "low", "close", "volume"]
    return out


def refresh_spy_history(csv_path: str = os.path.join("backtests", "spy_history.csv"),
                        fetch_fn=_yf_fetch) -> int:
    """Append recent SPY bars so the CSV stays current. Atomic write. Returns the
    number of new rows added."""
    import pandas as pd
    if not os.path.exists(csv_path):
        logger.warning(f"refresh_spy_history: {csv_path} missing")
        return 0
    old = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    old.index = pd.to_datetime(old.index)
    last = old.index.max().date()
    new = fetch_fn((last + timedelta(days=1)).isoformat())
    if new is None or not len(new):
        return 0
    new.index = pd.to_datetime(new.index)
    new = new[new.index.date > last]
    if not len(new):
        return 0
    combined = pd.concat([old, new])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.index.name = ""
    atomic_write_text(csv_path, combined.to_csv())
    logger.info(f"refresh_spy_history: appended {len(new)} rows, now through "
                f"{combined.index.max().date()}")
    return len(new)
