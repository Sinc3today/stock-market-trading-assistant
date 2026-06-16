"""tests/test_event_condor_wf.py -- FOMC+CPI condor with strike-placement sweep.

Extends the FOMC-condor lead to CPI and tests breach mitigation (wider shorts).
Reuses fomc_condor_wf pricing (already tested); here we cover the new breach
stats + event-list assembly. Real option pull runs in main().
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def test_loss_stats_breach_rate_and_worst():
    from backtests.event_condor_wf import loss_stats
    s = loss_stats([110.0, 120.0, -408.0, -34.0, -387.0], breach_threshold=-200.0)
    assert s["breach_rate"] == 40.0     # two of five worse than -200
    assert s["worst"] == -408.0


def test_loss_stats_empty():
    from backtests.event_condor_wf import loss_stats
    assert loss_stats([])["breach_rate"] == 0.0


def test_event_list_combines_fomc_and_cpi_in_window():
    from backtests.event_condor_wf import build_events
    evs = build_events(date(2025, 1, 1), date(2025, 3, 31))
    kinds = {k for k, _ in evs}
    assert kinds == {"FOMC", "CPI"}
    # sorted by date
    assert evs == sorted(evs, key=lambda e: e[1])
    # only in-window
    assert all(date(2025, 1, 1) <= d <= date(2025, 3, 31) for _, d in evs)


def test_wider_shorts_via_move_mult_reduces_breach_geometry():
    # placing shorts at a larger multiple of the expected move pushes them
    # further from spot -> requires a bigger move to breach (mitigation logic).
    from backtests.fomc_condor_wf import condor_strikes
    _, sp1, sc1, _ = condor_strikes(600.0, 10.0 * 1.0, width=5)
    _, sp2, sc2, _ = condor_strikes(600.0, 10.0 * 1.5, width=5)
    assert sp2 < sp1 and sc2 > sc1     # wider shorts at the higher multiple
