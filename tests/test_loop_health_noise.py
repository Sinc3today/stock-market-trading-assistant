"""tests/test_loop_health_noise.py -- the page must name a real problem.

The user got Pushover alerts titled "Learning loop needs attention". Neither
issue was about the learning loop:

  * "[yfinance] HTTP Error 404: No fundamentals data found for symbol: SPY" --
    we ASK an ETF for its earnings date. earnings_calendar's own docstring says
    "ETFs return an empty dict". yfinance logs its own 404 at ERROR, the stdlib
    logging bridge forwards it into app.log, scan_recent_log_errors greps for
    " | ERROR", and it becomes a page. A question we know has no answer.
  * "FRED observation error for UNRATE: HTTPError" -- transient, and reported
    with the exception CLASS NAME only, so 400 (bad key, fix now), 429 (back
    off) and 503 (wait) are indistinguishable.

scan_recent_log_errors greps for any ERROR/WARNING line, so ANY third-party
library that logs an error pages the user under a title claiming the learning
loop is broken. The loop was fine both days.

That is the same failure as the ERROR-level "add it to _CREDIT_STRATEGIES"
message fixed on 2026-09-09: a channel that fires for everything stops meaning
anything. The fix is the same shape -- separate the categories so the loud one
stays rare.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import loop_health as lh

_YF = ('2026-09-08 07:50:05.979 | ERROR    | logging:callHandlers:1706 - '
       '[yfinance] HTTP Error 404: {"quoteSummary":{"result":null,"error":'
       '{"code":"Not Found","description":"No fundamentals data found for '
       'symbol: SPY"}}}')
_FRED = ('2026-09-09 14:30:18.000 | ERROR    | data.fred_client:'
         'get_latest_observation:198 - FRED observation error for UNRATE: HTTPError')
_OURS = ('2026-09-09 09:16:00.000 | ERROR    | learning.paper_broker:'
         'execute_today:140 - paper broker exploded')


def test_third_party_noise_is_classified_as_external():
    issues = lh.summarize_error_lines([_YF] * 3)
    assert lh.classify_issues(issues)["external"]
    assert not lh.classify_issues(issues)["loop"]


def test_our_own_failures_are_classified_as_loop_issues():
    issues = lh.summarize_error_lines([_OURS] * 3)
    assert lh.classify_issues(issues)["loop"]
    assert not lh.classify_issues(issues)["external"]


def test_a_stale_artifact_is_always_a_loop_issue():
    """The thing this monitor exists for: the off-hours learner sat dead for
    ~5 weeks. That must never be filed as external noise."""
    c = lh.classify_issues(["off-hours learner last ran 2026-08-01 (40 days ago)"])
    assert c["loop"] and not c["external"]


def test_external_noise_alone_does_not_page_as_a_loop_failure(monkeypatch):
    """Two days of Pushover alerts titled "Learning loop needs attention" for
    an ETF earnings 404 and a transient FRED timeout. The loop was fine."""
    from learning import scheduler as sched
    monkeypatch.setattr(sched, "gather_and_assess", lambda: [], raising=False)
    monkeypatch.setattr("learning.loop_health.gather_and_assess",
                        lambda: ["3x ERROR: logging:callHandlers - [yfinance] "
                                 "HTTP Error 404 no fundamentals for SPY"])
    sent = []
    sched.job_loop_health(alert_fn=lambda title, body: sent.append((title, body)))
    assert not any("needs attention" in t.lower() for t, _ in sent), (
        f"external noise paged as a loop failure: {sent}")


def test_a_real_loop_failure_still_pages(monkeypatch):
    """Quieting the noise must not quiet the signal."""
    from learning import scheduler as sched
    monkeypatch.setattr("learning.loop_health.gather_and_assess",
                        lambda: ["off-hours learner last ran 2026-08-01 (40 days ago)"])
    sent = []
    sched.job_loop_health(alert_fn=lambda title, body: sent.append((title, body)))
    assert any("needs attention" in t.lower() for t, _ in sent), sent


def test_fred_and_yfinance_are_both_recognised_as_external():
    for line in (_YF, _FRED):
        issues = lh.summarize_error_lines([line] * 3)
        assert lh.classify_issues(issues)["external"], line[:60]


def test_classification_covers_every_issue_it_is_given():
    """Nothing may fall between the two buckets and vanish."""
    issues = lh.summarize_error_lines([_YF] * 3 + [_OURS] * 3)
    issues.append("spy_history.csv last row 2026-07-01 (70 days stale)")
    c = lh.classify_issues(issues)
    assert len(c["loop"]) + len(c["external"]) == len(issues)


def test_an_unrecognised_issue_defaults_to_loop_not_external():
    """If we cannot tell, treat it as ours. Silently downgrading an unknown
    failure to 'external noise' is how the off-hours learner died quietly."""
    c = lh.classify_issues(["something nobody anticipated happened"])
    assert c["loop"] and not c["external"]
