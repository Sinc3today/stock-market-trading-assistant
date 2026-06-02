# tests/test_intraday_scanner_structure.py
import os, sys
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scanners.intraday_scanner import build_intraday_structure


def _chain(contracts):
    class C:
        def get_chain(self, t, ct, mn, mx, strike_min=None, strike_max=None, limit=50):
            return [c for c in contracts if c["type"] == ct]
    return C()


def test_build_intraday_structure_enriches_setup():
    setup = {"strategy": "iron_condor", "dte_bucket": "0DTE", "direction": "neutral"}
    chain = _chain([
        {"type": "put",  "strike": 497.0, "mid": 1.20, "mark": 1.20, "expiration": "2026-06-01"},
        {"type": "put",  "strike": 492.0, "mid": 0.40, "mark": 0.40, "expiration": "2026-06-01"},
        {"type": "call", "strike": 503.0, "mid": 1.10, "mark": 1.10, "expiration": "2026-06-01"},
        {"type": "call", "strike": 508.0, "mid": 0.35, "mark": 0.35, "expiration": "2026-06-01"},
    ])
    enriched = build_intraday_structure(setup, spot=500.0, chain=chain, as_of=date(2026, 6, 1))
    assert enriched is not None
    assert round(enriched["entry_price"], 2) == 1.55
    assert len(enriched["legs"]) == 4
    assert enriched["strategy"] == "iron_condor"   # original fields preserved


def test_build_intraday_structure_none_when_unpriceable():
    setup = {"strategy": "iron_condor", "dte_bucket": "0DTE", "direction": "neutral"}
    enriched = build_intraday_structure(setup, spot=500.0, chain=_chain([]), as_of=date(2026, 6, 1))
    assert enriched is None


def test_build_intraday_structure_keeps_pricing_fields_for_book_assignment():
    # enriched must expose strategy/dte_bucket/max_profit/max_loss so the seam
    # can call assign_book. (Guards the fields the scanner reads.)
    setup = {"strategy": "iron_condor", "dte_bucket": "0DTE", "direction": "neutral"}
    chain = _chain([
        {"type": "put",  "strike": 497.0, "mid": 1.20, "mark": 1.20, "expiration": "2026-06-01"},
        {"type": "put",  "strike": 492.0, "mid": 0.40, "mark": 0.40, "expiration": "2026-06-01"},
        {"type": "call", "strike": 503.0, "mid": 1.10, "mark": 1.10, "expiration": "2026-06-01"},
        {"type": "call", "strike": 508.0, "mid": 0.35, "mark": 0.35, "expiration": "2026-06-01"},
    ])
    enriched = build_intraday_structure(setup, spot=500.0, chain=chain, as_of=date(2026, 6, 1))
    for k in ("strategy", "dte_bucket", "max_profit", "max_loss"):
        assert k in enriched
