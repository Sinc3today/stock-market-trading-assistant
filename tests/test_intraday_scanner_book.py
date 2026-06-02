import os, sys
from datetime import date
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_assign_book_applied_at_seam(monkeypatch):
    """The scanner computes book from assign_book on the enriched setup."""
    import config
    # Force the iron_condor/0DTE combo to demand a large target → learning.
    monkeypatch.setattr(config, "INTRADAY_FEASIBILITY",
                        {("iron_condor", "0DTE"): {"min_target_dollars": 1e9, "min_rr": 0.0}})
    from scanners.intraday_scanner import _assign_book_for_enriched
    enriched = {"strategy": "iron_condor", "dte_bucket": "0DTE",
                "max_profit": 100.0, "max_loss": 100.0}
    assert _assign_book_for_enriched(enriched) == "learning"
