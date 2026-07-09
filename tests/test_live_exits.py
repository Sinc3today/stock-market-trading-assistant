"""tests/test_live_exits.py -- exit alerts for the LIVE book (real money).

Paper positions auto-close at the 70% profit target / 21-DTE time stop, but the
user's real RH positions got NO exit signal (audit finding: only emergencies
fired). These alerts tell the user when a live position hits its profit target
or ages to the time exit — they close it manually on RH.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _trade(tid="L1", entry=1.55, size=2, max_profit=310.0, expiry="2026-07-24"):
    return {"trade_id": tid, "book": "live", "outcome": "open", "ticker": "SPY",
            "strategy": "iron_condor", "entry_price": entry, "size": size,
            "max_profit": max_profit,
            "legs": [{"action": "SELL", "option_type": "PUT", "strike": 700,
                      "expiry": expiry}]}


class _Rec:
    def __init__(self, trades): self._t = trades
    def get_open_trades(self): return self._t


class _Push:
    def __init__(self): self.sent = []
    def send(self, title, message, priority=0, **k):
        self.sent.append((title, priority)); return True


def test_profit_target_hit_fires_once():
    from alerts.live_exits import check_live_exits
    push, alerted = _Push(), set()
    # far expiry so ONLY the profit-target path is in play
    t = _trade(expiry="2026-08-21")
    # quote_fn returns MTM dollars: 80% of the $310 max profit
    n = check_live_exits(_Rec([t]), push, alerted,
                         mtm_fn=lambda t: 248.0, today=date(2026, 7, 9))
    assert n == 1 and len(push.sent) == 1
    assert "profit" in push.sent[0][0].lower()
    assert push.sent[0][1] == 1                      # high, not emergency
    # dedupe: second pass fires nothing
    assert check_live_exits(_Rec([t]), push, alerted,
                            mtm_fn=lambda t: 260.0, today=date(2026, 7, 9)) == 0


def test_below_target_no_alert():
    from alerts.live_exits import check_live_exits
    push = _Push()
    n = check_live_exits(_Rec([_trade(expiry="2026-08-21")]), push, set(),
                         mtm_fn=lambda t: 100.0, today=date(2026, 7, 9))  # 32%
    assert n == 0 and push.sent == []


def test_dte_time_exit_fires():
    from alerts.live_exits import check_live_exits
    push = _Push()
    # expiry 07-24, today 07-06 -> 18 DTE <= 21 -> time-exit alert even with low MTM
    n = check_live_exits(_Rec([_trade()]), push, set(),
                         mtm_fn=lambda t: 50.0, today=date(2026, 7, 6))
    assert n == 1
    assert "dte" in push.sent[0][0].lower() or "time" in push.sent[0][0].lower()


def test_mtm_failure_still_checks_dte_and_never_raises():
    from alerts.live_exits import check_live_exits
    push = _Push()
    def boom(t): raise RuntimeError("quotes down")
    # far from expiry + broken quotes -> no alert, no crash
    assert check_live_exits(_Rec([_trade(expiry="2026-08-21")]), push, set(),
                            mtm_fn=boom, today=date(2026, 7, 9)) == 0
    # near expiry + broken quotes -> DTE alert still fires
    assert check_live_exits(_Rec([_trade()]), push, set(),
                            mtm_fn=boom, today=date(2026, 7, 6)) == 1


def test_ignores_paper_books():
    from alerts.live_exits import check_live_exits
    t = _trade(); t["book"] = "disciplined"
    push = _Push()
    assert check_live_exits(_Rec([t]), push, set(),
                            mtm_fn=lambda t: 300.0, today=date(2026, 7, 9)) == 0
