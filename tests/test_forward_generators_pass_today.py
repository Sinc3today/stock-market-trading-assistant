"""tests/test_forward_generators_pass_today.py -- a date you are given is the date you use.

test_ladder_forward::test_closes_at_the_rung_time_stop passed on 2026-09-10 and
failed on 2026-09-13 with no code change. maybe_open_ladder accepts `today`,
but called build_condor(spy, vix, dte=dte) without it, and the builder fell
back to date.today() — the real clock — to pick the expiry. The position's
expiration therefore depended on the day the code happened to run.

Live trading never noticed: the job passes the real date, so both agree during
market hours. Anything that runs a generator for a date other than today —
a test, a backfill, a replay — silently gets a different trade.

Enumeration found it in 3 of the 4 forward-test generators (ladder, seven_dte,
qqq_condor; broken_wing already passed it). This test enforces the rule across
every generator, so a fifth one cannot reintroduce it.
"""
from __future__ import annotations

import ast
import glob
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILDERS = {"build_condor", "build_butterfly", "build_broken_wing"}


def _violations() -> list[str]:
    found = []
    for path in sorted(glob.glob(os.path.join(ROOT, "learning", "*_forward.py"))):
        tree = ast.parse(open(path).read())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            if "today" not in params:
                continue
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name in BUILDERS and not any(k.arg == "today" for k in call.keywords):
                    rel = os.path.relpath(path, ROOT)
                    found.append(f"{rel}:{call.lineno} {fn.name}() calls {name} without today=")
    return found


def test_every_forward_generator_passes_its_date_to_the_structure_builder():
    v = _violations()
    assert v == [], (
        "these generators accept `today` but let the builder read the real "
        "clock instead, so a replay or test gets a different expiry:\n  "
        + "\n  ".join(v))


def test_the_scan_actually_sees_the_generators():
    """A scanner that matches nothing passes vacuously."""
    paths = glob.glob(os.path.join(ROOT, "learning", "*_forward.py"))
    names = {os.path.basename(p) for p in paths}
    assert {"ladder_forward.py", "seven_dte_forward.py", "qqq_condor_forward.py",
            "broken_wing_forward.py"} <= names
