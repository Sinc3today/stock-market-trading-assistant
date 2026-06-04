"""
tests/test_outcome_resolver_shadow.py -- Task 4: outcome_resolver stamps the
same-day directional result on shadow book trades during extension-skip days.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


def test_skip_day_stamps_shadow_directional(iso):
    from journal.trade_recorder import TradeRecorder
    from learning.predictions import PredictionLog, Prediction
    from learning.outcome_resolver import OutcomeResolver

    rec = TradeRecorder()
    tid = rec.log_entry(
        ticker="SPY",
        entry_price=1.2,
        size=1,
        trade_type="credit_spread",
        strategy="credit_spread",
        book="shadow",
        source="auto-paper",
        legs=[{"action": "SELL", "type": "put", "strike": 755}],
    )
    # Stamp entry_spy and a today-matching entry_date on the shadow trade
    trades = rec.get_all_trades()
    for t in trades:
        if t["trade_id"] == tid:
            t["entry_spy"] = 760.0
            t["entry_date"] = "2026-06-03 09:16 AM EST"
    rec._save(trades)

    # Use PredictionLog.save(Prediction(...)) — the real API (no .log() method)
    preds = PredictionLog()
    preds.save(Prediction(
        date="2026-06-03",
        regime="trending_up_calm",
        direction="bullish",
        tradeable=False,
        entry_spy=760.0,
        confidence=0.0,
    ))

    class _Poly:
        def get_bars(self, *a, **k):
            import pandas as pd
            return pd.DataFrame({"close": [766.0]})   # SPY closed UP -> bullish correct

    OutcomeResolver(
        polygon_client=_Poly(),
        trade_recorder=rec,
        prediction_log=preds,
    ).resolve_today(today=date(2026, 6, 3))

    t = rec.get_trade_by_id(tid)
    assert t["shadow_directional"] == "correct"   # 766 > 760 entry
