"""tests/test_slippage.py -- slippage math + observation store (pure/testable).

The bot's paper marks use day-close/vwap (optimistic — no bid/ask spread). This
measures the real fill-quality gap: what a real RH fill cost vs the assumed mark.
The live RH fetch (Playwright) is deferred; this is the part we can verify now.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_credit_entry_worse_fill_is_positive_cost():
    from journal.slippage import compute_slippage
    # Iron condor: assumed credit 1.10, real fill only 1.00 -> got 0.10 less.
    s = compute_slippage(mark_price=1.10, fill_price=1.00, action="credit",
                         contracts=1)
    assert round(s["slippage_per_share"], 4) == 0.10
    assert round(s["slippage_dollars"], 2) == 10.00      # 0.10 * 1 * 100
    assert round(s["slippage_pct"], 2) == pytest.approx(9.09, abs=0.05)


def test_debit_entry_worse_fill_is_positive_cost():
    from journal.slippage import compute_slippage
    # Debit spread: assumed pay 2.00, real fill 2.10 -> paid 0.10 more.
    s = compute_slippage(mark_price=2.00, fill_price=2.10, action="debit",
                         contracts=1)
    assert round(s["slippage_per_share"], 4) == 0.10
    assert round(s["slippage_dollars"], 2) == 10.00


def test_favorable_fill_is_negative_cost():
    from journal.slippage import compute_slippage
    # Credit: filled BETTER than mark (1.05 vs 1.00) -> negative cost (you won).
    s = compute_slippage(mark_price=1.00, fill_price=1.05, action="credit")
    assert s["slippage_per_share"] < 0
    assert s["slippage_dollars"] < 0


def test_contracts_scale_dollars():
    from journal.slippage import compute_slippage
    s = compute_slippage(mark_price=1.10, fill_price=1.00, action="credit",
                         contracts=3)
    assert round(s["slippage_dollars"], 2) == 30.00      # 0.10 * 3 * 100


def test_store_append_and_summary(tmp_path):
    from journal.slippage import SlippageStore, compute_slippage
    path = str(tmp_path / "slippage.jsonl")
    store = SlippageStore(path)
    store.record({"trade_id": "A1", **compute_slippage(1.10, 1.00, action="credit")})
    store.record({"trade_id": "A2", **compute_slippage(2.00, 2.10, action="debit")})
    rows = store.all()
    assert len(rows) == 2
    summary = store.summary()
    assert summary["count"] == 2
    assert round(summary["total_dollars"], 2) == 20.00   # 10 + 10
    assert round(summary["avg_dollars"], 2) == 10.00


def test_store_survives_reload(tmp_path):
    from journal.slippage import SlippageStore, compute_slippage
    path = str(tmp_path / "slippage.jsonl")
    SlippageStore(path).record({"trade_id": "A1",
                                **compute_slippage(1.10, 1.00, action="credit")})
    # fresh instance reads what was persisted
    assert len(SlippageStore(path).all()) == 1
