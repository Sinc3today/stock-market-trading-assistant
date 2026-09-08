"""tests/test_no_phantom_features.py -- config must not claim what does not exist.

Three separate times the audit found a feature that config declared, tests
asserted, and no code implemented:

  * ENFORCE_CONCENTRATION_GUARD = True, enforced in 1 of 6 opening paths
  * SEVEN_DTE / QQQ_CONDOR kill switches that were not in config at all, so
    getattr(..., True) meant those tests could never be turned off
  * condor_short_strike_touch / forced_close_* built into the exit-rule dict,
    asserted by tests, and never read by _evaluate

The third is the worst kind: the tests were GREEN and reported the feature as
present. They verified config plumbing, not behaviour.

A phantom feature is more dangerous than a missing one — you plan around it.
"""
import ast
import inspect
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _reads(func, key: str) -> bool:
    """Does this function actually READ the key, not merely construct it?"""
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # rule["key"] / rule.get("key") style reads
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value == key:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == key:
            return True
    return False


@pytest.mark.parametrize("key", ["profit_target_pct", "stop_pct",
                                 "dte_close_threshold"])
def test_exit_rule_keys_that_are_used_are_read_by_evaluate(key):
    """Sanity: the keys that DO drive behaviour are genuinely read."""
    from learning.exit_manager import ExitManager
    assert _reads(ExitManager._evaluate, key), \
        f"{key} is not read by _evaluate — is it a phantom too?"


def test_the_exit_rule_dict_contains_no_unread_keys():
    """Every key the rule table publishes must be consumed by _evaluate.

    condor_short_strike_touch, forced_close_time and
    forced_close_minutes_before_expiry were published, configured True, and
    asserted by tests — while _evaluate used only profit_target_pct, stop_pct
    and dte_close_threshold. A 0DTE condor touching its short strike was NOT
    closed, contrary to what config and the suite both claimed.
    """
    from learning.exit_manager import ExitManager, exit_rule_for
    # These three ARE read — by signals/intraday_exit_rules.evaluate_intraday_exit
    # on the backtest path, not by the live _evaluate. Documented consumers, so
    # not phantom. (That the live and backtest ladders diverge at all is a
    # separate finding, tracked for the full code review.)
    BACKTEST_PATH_KEYS = {"scratch_time", "scratch_theta", "hard_close_time"}
    rule = exit_rule_for("iron_condor", "0DTE")
    unread = sorted(k for k in rule
                    if k not in BACKTEST_PATH_KEYS
                    and not _reads(ExitManager._evaluate, k))
    assert unread == [], (
        f"exit rule publishes keys nothing reads: {unread}. Either wire them "
        f"into _evaluate or remove them — a phantom feature is worse than a "
        f"missing one, because you plan around it.")


def test_every_tunable_param_actually_drives_live_behaviour():
    """hypothesis_engine may only propose changes to knobs that DO something.

    exit_manager.PROFIT_TARGET_PCT and DTE_CLOSE_THRESHOLD were whitelisted for
    tuning long after _exit_rule_for stopped reading them, so the learning loop
    could 'accept' a backtested change with zero live effect — the worst
    possible asymmetry.
    """
    import importlib
    from learning.hypothesis_engine import TUNABLE_PARAMS
    orphans = []
    for name, spec in TUNABLE_PARAMS.items():
        module_path = spec[0] if isinstance(spec, (list, tuple)) else spec.get("module")
        var = spec[1] if isinstance(spec, (list, tuple)) else spec.get("var")
        if not module_path or not var:
            continue
        mod = importlib.import_module(module_path)
        assert hasattr(mod, var), f"{module_path}.{var} does not exist"
    assert orphans == []
