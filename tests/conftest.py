"""tests/conftest.py -- shared isolation for the whole suite.

Written 2026-09-07. Until now there was no conftest at all, and 66 test files
hand-rolled `config.LOG_DIR` isolation in four different idioms. Two real
consequences:

  1. Any test that FORGETS writes into the live journal — logs/trades.json is
     the system of record for real money.
  2. Module-level caches leak between tests. data/vix_client keeps `_cache` as
     a module global, so two VIX tests passed or failed depending on run
     order — found while fixing the VIX fabrication bug the same day.

The isolation here is autouse and therefore applies even to a test that never
thinks about it. A handful of tests genuinely need the real journal (the
scorecard smoke test, the enumeration vocabulary scan); those opt out with

    @pytest.mark.live_journal

which is deliberately noisy to write, so reading production data is a visible
decision rather than an accident.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_journal: test reads the REAL logs/ directory instead of tmp_path",
    )


@pytest.fixture(autouse=True)
def isolate_log_dir(request, tmp_path, monkeypatch):
    """Point config.LOG_DIR at a per-test tmp dir unless explicitly opted out.

    Runs before every test, so a file that forgets to isolate cannot touch the
    live journal. Tests that already set LOG_DIR themselves are unaffected —
    theirs simply wins, being applied later.
    """
    if request.node.get_closest_marker("live_journal"):
        yield None
        return
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    yield tmp_path


@pytest.fixture(autouse=True)
def reset_module_caches():
    """Clear module-level caches that would otherwise leak across tests.

    A cache held as a module global is invisible to per-instance setup, which
    is exactly how two VIX tests became order-dependent.
    """
    def _clear():
        try:
            import data.vix_client as vix_mod
            vix_mod._cache.update({"vix": None, "fetched_at": None,
                                   "df": None, "df_at": None})
        except Exception:
            pass

    _clear()
    yield
    _clear()
