"""tests/test_loop_health.py -- learning-loop health monitor + CSV auto-refresh.

The off-hours learner died silently for ~5 weeks and predictions were mis-scored
for weeks — both found only by manual inspection. assess_health() turns artifact
freshness into a list of issues so silent degradation surfaces in days, and
refresh_spy_history() keeps the replay CSV from going stale in the first place.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def test_assess_health_all_fresh_no_issues():
    from learning.loop_health import assess_health
    today = date(2026, 6, 29)
    issues = assess_health(
        today,
        last_offhours_date=date(2026, 6, 28),
        last_prediction_date=date(2026, 6, 26),
        last_kb_date=date(2026, 6, 27),
        csv_last_date=date(2026, 6, 26),
        rh_last_sync_date=date(2026, 6, 28),
    )
    assert issues == []


def test_assess_health_flags_stale_offhours_and_csv():
    from learning.loop_health import assess_health
    today = date(2026, 6, 29)
    issues = assess_health(
        today,
        last_offhours_date=date(2026, 5, 24),    # ~5 weeks stale
        last_prediction_date=date(2026, 6, 26),
        last_kb_date=date(2026, 6, 27),
        csv_last_date=date(2026, 5, 22),         # stale
        rh_last_sync_date=date(2026, 6, 28),
    )
    joined = " ".join(issues).lower()
    assert "off-hours" in joined
    assert "spy_history" in joined or "csv" in joined
    assert len(issues) == 2


def test_assess_health_flags_missing_artifacts():
    from learning.loop_health import assess_health
    issues = assess_health(
        date(2026, 6, 29),
        last_offhours_date=None,
        last_prediction_date=None,
        last_kb_date=None,
        csv_last_date=None,
        rh_last_sync_date=None,
    )
    assert len(issues) == 5   # every component flagged


def test_refresh_spy_history_appends_and_is_idempotent(tmp_path):
    from learning.loop_health import refresh_spy_history
    csv = tmp_path / "spy.csv"
    base = pd.DataFrame(
        {"open": [400, 401], "high": [402, 403], "low": [399, 400],
         "close": [401, 402], "volume": [1_000_000, 1_100_000]},
        index=pd.to_datetime(["2026-06-25", "2026-06-26"]),
    )
    base.index.name = ""
    base.to_csv(csv)

    def fake_fetch(start):
        return pd.DataFrame(
            {"open": [403], "high": [405], "low": [402], "close": [404],
             "volume": [1_200_000]},
            index=pd.to_datetime(["2026-06-29"]),
        )

    added = refresh_spy_history(str(csv), fetch_fn=fake_fetch)
    assert added == 1
    out = pd.read_csv(csv, index_col=0, parse_dates=True)
    assert len(out) == 3
    assert str(out.index.max().date()) == "2026-06-29"
    # idempotent: same fetch returns a row that's already present -> 0 added
    assert refresh_spy_history(str(csv), fetch_fn=fake_fetch) == 0
