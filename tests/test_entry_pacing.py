"""tests/test_entry_pacing.py -- smart book caps + entry pacing (2026-07-10).

User rules: the 1-3DTE cycle gets its own slot (45DTE can't crowd it out);
at most 2 disciplined opens/day; never two opens close together in time
(entries minutes apart ride the same SPY move = correlated losses); and
execute_signal now runs the strike-concentration guard too.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

EAST = pytz.timezone("US/Eastern")


@pytest.fixture(autouse=True)
def _window(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def _setup(bucket="1-3DTE", sp=740.0, sc=760.0):
    return {"date": "2026-07-10", "strategy": "iron_condor", "dte_bucket": bucket,
            "book": "disciplined", "direction": "neutral", "entry_price": 1.9,
            "max_profit": 190.0, "max_loss": 310.0,
            "legs": [{"action": "SELL", "option_type": "PUT",  "strike": sp},
                     {"action": "BUY",  "option_type": "PUT",  "strike": sp - 5},
                     {"action": "SELL", "option_type": "CALL", "strike": sc},
                     {"action": "BUY",  "option_type": "CALL", "strike": sc + 5}]}


def _fill_45dte_book(rec, n=3):
    for i in range(n):
        rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="iron_condor",
                      strategy="iron_condor", direction="neutral",
                      legs=[{"action": "SELL", "option_type": "PUT", "strike": 600.0 + i * 40},
                            {"action": "SELL", "option_type": "CALL", "strike": 900.0 + i * 40}],
                      dte_bucket="45DTE", book="disciplined")
    # these represent positions opened on PRIOR days — backdate the stamps so
    # they don't consume today's daily-open / spacing budget
    trades = rec.get_all_trades()
    for t in trades:
        t["entry_date"] = "2026-07-01 09:45 AM EST"
    rec._save(trades)


def test_short_dte_has_its_own_slot(iso, monkeypatch):
    # 45DTE book full (3/3) must NOT block a 1-3DTE open anymore
    from journal.trade_recorder import TradeRecorder
    from learning.paper_broker import PaperBroker
    rec = TradeRecorder()
    _fill_45dte_book(rec)
    r = PaperBroker().execute_signal(_setup())
    assert r["recorded"] is True


def test_short_dte_slot_cap_enforced(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.paper_broker import PaperBroker
    import learning.paper_broker as pb
    broker = PaperBroker()
    assert broker.execute_signal(_setup(sp=700, sc=800))["recorded"]
    # second short-DTE while one is open -> blocked by its own 1-slot budget
    # (spacing/daily checks are bypassed to isolate the cap under test)
    r = broker.execute_signal({**_setup(sp=640, sc=860),
                               "_test_bypass_pacing": True})
    assert r["recorded"] is False and "short_dte" in str(r.get("skipped_reason", ""))


def test_daily_open_limit(iso, monkeypatch):
    import config
    from learning.paper_broker import PaperBroker
    monkeypatch.setattr(config, "MAX_DAILY_DISCIPLINED_OPENS", 1)
    monkeypatch.setattr(config, "MIN_ENTRY_SPACING_MIN", 0)
    broker = PaperBroker()
    assert broker.execute_signal(_setup(sp=700, sc=800))["recorded"]
    # cap=1/day -> a 45DTE-style second open is also refused (count is book-wide)
    monkeypatch.setattr(config, "MAX_CONCURRENT_SHORT_DTE", 5)
    r = broker.execute_signal(_setup(sp=640, sc=860))
    assert r["recorded"] is False and "daily" in str(r.get("skipped_reason", ""))


def test_entry_spacing_blocks_back_to_back(iso, monkeypatch):
    import config
    from learning.paper_broker import PaperBroker
    monkeypatch.setattr(config, "MAX_CONCURRENT_SHORT_DTE", 5)
    monkeypatch.setattr(config, "MAX_DAILY_DISCIPLINED_OPENS", 5)
    broker = PaperBroker()
    assert broker.execute_signal(_setup(sp=700, sc=800))["recorded"]
    # seconds later, far strikes, room in every cap -> still refused: too soon
    r = broker.execute_signal(_setup(sp=640, sc=860))
    assert r["recorded"] is False and "spacing" in str(r.get("skipped_reason", ""))


def test_execute_signal_now_runs_concentration_guard(iso, monkeypatch):
    import config
    from learning.paper_broker import PaperBroker
    monkeypatch.setattr(config, "MIN_ENTRY_SPACING_MIN", 0)
    broker = PaperBroker()
    assert broker.execute_signal(_setup(sp=700, sc=800))["recorded"]
    monkeypatch.setattr(config, "MAX_CONCURRENT_SHORT_DTE", 5)
    # overlapping short put (701 vs 700) -> the strike guard fires
    r = broker.execute_signal(_setup(sp=701, sc=860))
    assert r["recorded"] is False and "concentration" in str(r.get("skipped", ""))
