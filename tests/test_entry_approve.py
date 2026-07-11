"""tests/test_entry_approve.py -- entry-approve emergency alert (pure builder).

When a tradeable daily play opens (09:45, in-window), the user gets a can't-miss
emergency Pushover with the RH-shaped legs + a one-tap link to /copilot to place
it. The builder is pure; the send is a thin priority-2 wrapper.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _condor_trade():
    return {
        "trade_id": "AB12CD34", "ticker": "SPY", "strategy": "iron_condor",
        "entry_price": 1.10, "max_profit": 110.0, "max_loss": -390.0,
        "legs": [
            {"action": "BUY",  "option_type": "PUT",  "strike": 695, "expiry": "2026-07-17"},
            {"action": "SELL", "option_type": "PUT",  "strike": 700, "expiry": "2026-07-17"},
            {"action": "SELL", "option_type": "CALL", "strike": 776, "expiry": "2026-07-17"},
            {"action": "BUY",  "option_type": "CALL", "strike": 781, "expiry": "2026-07-17"},
        ],
    }


def test_build_approve_alert_has_legs_expiry_and_copilot_link():
    from alerts.entry_approve import build_approve_alert
    a = build_approve_alert(_condor_trade(), base_url="http://nucbox:8002")
    # title names the trade
    assert "SPY" in a["title"] and "iron condor" in a["title"].lower()
    # body carries the ordered RH-shaped legs (buy call, sell call, buy put, sell put)
    assert "BUY $781 CALL" in a["body"]
    assert "SELL $776 CALL" in a["body"]
    assert "BUY $695 PUT" in a["body"]
    assert "SELL $700 PUT" in a["body"]
    # body shows expiry + net credit/debit
    assert "07-17-26" in a["body"]       # house display style (MM-DD-YY)
    assert "1.1" in a["body"]
    # one-tap link straight to the copilot screen
    assert a["url"] == "http://nucbox:8002/copilot"
    assert a["url_title"]


def _short_dte_condor():
    t = _condor_trade()
    t["dte_bucket"] = "1-3DTE"
    return t


def test_friday_short_dte_condor_gets_weekend_theta_tag():
    # DOW study (docs/DOW_STUDY.md): Fri-entered 2-day condors harvest 3 days of
    # weekend theta for 1 day of market risk — 81% hist. win. Tag the alert.
    from alerts.entry_approve import build_approve_alert
    from datetime import date
    a = build_approve_alert(_short_dte_condor(), base_url=None,
                            today=date(2026, 7, 10))          # a Friday
    assert "weekend theta" in a["body"]


def test_monday_and_wednesday_notes():
    from alerts.entry_approve import build_approve_alert
    from datetime import date
    mon = build_approve_alert(_short_dte_condor(), base_url=None,
                              today=date(2026, 7, 13))
    assert "strong entry day" in mon["body"]
    wed = build_approve_alert(_short_dte_condor(), base_url=None,
                              today=date(2026, 7, 15))
    assert "weakest entry day" in wed["body"]


def test_no_dow_tag_for_45dte_or_neutral_days():
    from alerts.entry_approve import build_approve_alert
    from datetime import date
    # 45DTE condor: the study only covered the short-DTE cycle — no tag
    a45 = build_approve_alert(_condor_trade(), base_url=None,
                              today=date(2026, 7, 10))
    assert "theta" not in a45["body"] and "entry day" not in a45["body"]
    # Tuesday short-DTE: unremarkable day -> no tag
    tue = build_approve_alert(_short_dte_condor(), base_url=None,
                              today=date(2026, 7, 14))
    assert "entry day" not in tue["body"] and "weekend theta" not in tue["body"]


def test_build_approve_alert_without_base_url_has_no_link():
    from alerts.entry_approve import build_approve_alert
    a = build_approve_alert(_condor_trade(), base_url=None)
    assert a["url"] is None


def test_notify_entry_approve_sends_emergency_priority():
    from alerts.entry_approve import notify_entry_approve

    class _Push:
        def __init__(self): self.calls = []
        def send(self, title, message, url=None, url_title=None, priority=0, **k):
            self.calls.append({"priority": priority, "url": url, "title": title})
            return True

    push = _Push()
    ok = notify_entry_approve(_condor_trade(), push, base_url="http://nucbox:8002")
    assert ok is True
    assert len(push.calls) == 1
    assert push.calls[0]["priority"] == 2          # emergency — nags until acked
    assert push.calls[0]["url"] == "http://nucbox:8002/copilot"


def test_friday_tag_flips_to_warning_on_stressed_tape():
    # Gap study: VIX>20 or a >1% down Friday = 3-7x weekend breach risk
    from alerts.entry_approve import build_approve_alert
    from datetime import date
    hot = build_approve_alert(_short_dte_condor(), base_url=None,
                              today=date(2026, 7, 10), vix=24.0)
    assert "stressed tape" in hot["body"] and "weekend theta" not in hot["body"]
    down = build_approve_alert(_short_dte_condor(), base_url=None,
                               today=date(2026, 7, 10), vix=16.0, day_ret_pct=-1.4)
    assert "stressed tape" in down["body"]
    calm = build_approve_alert(_short_dte_condor(), base_url=None,
                               today=date(2026, 7, 10), vix=15.0, day_ret_pct=0.3)
    assert "weekend theta ✓" in calm["body"]
