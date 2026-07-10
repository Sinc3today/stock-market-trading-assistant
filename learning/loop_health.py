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


MEMORY_ALERT_MB = 1500   # bot RSS above this = trending toward the 06-15 freeze

# ── Error scan (2026-07-10) ──────────────────────────────────────────────────
# The webpush bug logged ~30 identical warnings in a day and NOTHING surfaced
# them — the user found it by noticing missing notifications (same story as the
# RH-token expiry: 56 errors, unwatched). The daily health push now includes any
# error signature repeating past these thresholds in the last 24h of app logs.
ERRORSCAN_ERROR_MIN   = 3    # same ERROR signature this many times -> flag
ERRORSCAN_WARNING_MIN = 10   # warnings are noisier; higher bar
ERRORSCAN_MAX_ISSUES  = 5    # cap the push size

_LOG_LINE = None  # compiled lazily


def _log_pattern():
    global _LOG_LINE
    if _LOG_LINE is None:
        import re
        _LOG_LINE = re.compile(
            r"^(\d{4}-\d{2}-\d{2}) [\d:.]+ \| (ERROR|WARNING)\s*\| ([^-]+) - (.*)$")
    return _LOG_LINE


def _signature(module: str, msg: str) -> str:
    """Stable signature: module:fn + message with volatile bits (digits) blanked,
    so 'retry 1 of 3' and 'retry 2 of 3' count as one recurring problem."""
    import re
    mod = module.strip().rsplit(":", 1)[0]          # drop the line number
    norm = re.sub(r"\d+", "#", msg.strip())[:90]
    return f"{mod} - {norm}"


def summarize_error_lines(lines, *, error_min: int = ERRORSCAN_ERROR_MIN,
                          warning_min: int = ERRORSCAN_WARNING_MIN) -> list[str]:
    """Pure: collapse ERROR/WARNING log lines into repeated-signature issues."""
    counts: dict[tuple, dict] = {}
    pat = _log_pattern()
    for line in lines:
        m = pat.match(line.strip())
        if not m:
            continue
        _, level, module, msg = m.groups()
        key = (level, _signature(module, msg))
        rec = counts.setdefault(key, {"n": 0, "sample": msg.strip()[:120]})
        rec["n"] += 1
    issues = []
    for (level, sig), rec in sorted(counts.items(), key=lambda kv: -kv[1]["n"]):
        floor = error_min if level == "ERROR" else warning_min
        if rec["n"] >= floor:
            issues.append(f"{rec['n']}× {level}: {sig}")
    return issues[:ERRORSCAN_MAX_ISSUES]


def scan_recent_log_errors(hours: int = 24) -> list[str]:
    """Read the app log files touched in the last `hours` and surface repeated
    error signatures (lines older than the cutoff are excluded by timestamp)."""
    import glob
    import time
    from datetime import datetime, timedelta
    cutoff_dt = datetime.now() - timedelta(hours=hours)
    cutoff_date = cutoff_dt.strftime("%Y-%m-%d")
    lines = []
    try:
        candidates = glob.glob(os.path.join(config.LOG_DIR, "app*.log"))
        recent = [f for f in candidates
                  if time.time() - os.path.getmtime(f) < hours * 3600 + 3600]
        for f in recent:
            with open(f, errors="replace") as fh:
                for line in fh:
                    if " | ERROR" in line or " | WARNING" in line:
                        if line[:10] >= cutoff_date:   # coarse day filter is enough
                            lines.append(line)
    except OSError as e:
        logger.warning(f"error scan could not read logs: {e}")
        return []
    return summarize_error_lines(lines)


def _memory_issues() -> list[str]:
    """Proactive memory check (T4#17): the 2026-06-15 freeze came from memory
    pressure with zero warning. Flag the bot's own RSS before systemd has to act."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_mb = int(line.split()[1]) // 1024
                    if rss_mb > MEMORY_ALERT_MB:
                        return [f"bot memory high ({rss_mb} MB RSS > {MEMORY_ALERT_MB} MB)"]
                    break
    except OSError:
        pass
    return []


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
    issues = _memory_issues() + scan_recent_log_errors()
    return issues + assess_health(
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
