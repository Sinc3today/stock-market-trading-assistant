"""tests/test_enumeration.py -- make a partial fix impossible.

Written 2026-09-07 after the same defect was fixed three times, in three
files, and still had ten instances live. A findings list tells you where
someone already looked; it cannot tell you where else the shape lives.

These tests fail when a NEW instance appears, which is the whole point:
they turn "did we get them all?" from a judgement call into a test result.

    .venv/bin/python -m tools.enumerate_conventions   # to see the list
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import enumerate_conventions as ec


DELIBERATELY_UNKNOWN = {
    "custom",         # rh_sync: a 3-leg or 5+-leg shape it cannot name
    "none",           # legacy journal placeholder, predates the fix
    "option_spread",  # a trade_type, never a strategy
}


def test_every_producer_name_resolves_or_is_deliberately_unknown():
    from journal.trade_recorder import _pnl_convention
    names = set(ec.strategy_vocabulary()) | ec.journal_vocabulary()
    gaps = sorted(n for n in names
                  if _pnl_convention(n) is None and n not in DELIBERATELY_UNKNOWN)
    assert gaps == [], (
        f"producers emit strategy names the convention owner cannot classify: "
        f"{gaps}. Teach journal.trade_recorder._pnl_convention, or add to "
        f"DELIBERATELY_UNKNOWN with a reason.")


def test_no_module_holds_its_own_credit_debit_list():
    priv = ec.private_strategy_lists()
    assert priv == [], (
        f"private strategy lists outside the convention owner: {priv}. "
        f"Route through _pnl_convention, or add to ALLOWED_LISTS with a reason.")


def test_the_allowlist_documents_a_reason_for_every_entry():
    for key, reason in ec.ALLOWED_LISTS.items():
        assert isinstance(reason, str) and len(reason) > 15, \
            f"{key} is allowlisted without a real reason"


def test_price_resolvers_never_return_a_hardcoded_number():
    """Known-remaining sites are listed explicitly, so a NEW one fails."""
    KNOWN = {
        "alerts/copilot_log.py", "alerts/live_exits.py", "alerts/web_app.py",
        "learning/journal_repair.py", "learning/rh_sync.py",
        "scanners/options_flow_scanner.py",
    }
    fresh = sorted({f for f, _, _ in ec.placeholder_prices() if f not in KNOWN})
    assert fresh == [], (
        f"new placeholder-price sites: {fresh}. A missing price is not a "
        f"price — return None and let the caller refuse.")


def test_the_enumerator_actually_finds_things():
    """A check that can never fail is not a check. Prove it has teeth."""
    assert ec.strategy_vocabulary(), "vocabulary scan found nothing"
    assert ec.private_strategy_lists(include_allowed=True), \
        "private-list scan found nothing even including allowed"
    assert ec.placeholder_prices(), "placeholder scan found nothing"


# ── forward-test consolidation (Phase 1, 2026-09-07) ─────────────

FORWARD_MODULES = ("seven_dte_forward", "qqq_condor_forward",
                   "broken_wing_forward", "ladder_forward")


def test_no_generator_reimplements_the_resolve_loop():
    """Every defect in the audit existed in SOME BUT NOT ALL of five
    near-identical resolvers. A new copy reintroduces exactly that."""
    import importlib
    import inspect
    for name in FORWARD_MODULES:
        mod = importlib.import_module(f"learning.{name}")
        src = inspect.getsource(mod)
        assert "_mark_spread" not in src, \
            f"{name} marks positions itself instead of using the shared core"
        assert "ForwardTest" in src, f"{name} does not use the shared core"


def test_every_generator_declares_a_spec():
    import importlib
    from learning.forward_test import ForwardSpec
    for name in FORWARD_MODULES:
        mod = importlib.import_module(f"learning.{name}")
        assert isinstance(getattr(mod, "SPEC", None), ForwardSpec), \
            f"{name} has no ForwardSpec"


def test_every_generator_has_a_working_kill_switch():
    """Two forward tests used getattr(config, FLAG, True) against flags that do
    not exist in config.py — they could not be turned off at all."""
    import importlib
    import config
    for name in FORWARD_MODULES:
        mod = importlib.import_module(f"learning.{name}")
        flag = mod.SPEC.enabled_flag
        assert flag, f"{name} has no kill switch"
        assert hasattr(config, flag), \
            f"{name}'s kill switch {flag} does not exist in config.py"


def test_every_generator_filters_to_its_own_book():
    """qqq_condor_forward lacked this, so on promotion both its resolver and
    the ExitManager would have managed the position."""
    import importlib
    for name in FORWARD_MODULES:
        mod = importlib.import_module(f"learning.{name}")
        assert mod.SPEC.book, f"{name} does not declare a book"


def test_no_generator_scales_one_rung_exit_from_another():
    """Proportional scaling of a time rule is invalid — theta is convex in DTE.
    Every close-DTE must be an explicit per-bucket number."""
    import importlib
    for name in FORWARD_MODULES:
        mod = importlib.import_module(f"learning.{name}")
        for bucket, close_dte in mod.SPEC.buckets.items():
            assert isinstance(close_dte, int), \
                f"{name}:{bucket} close-DTE is computed, not declared"
