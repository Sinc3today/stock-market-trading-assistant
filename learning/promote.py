"""
learning/promote.py -- Apply an accepted hypothesis to source code.

The Saturday weekly hypothesis loop produces JSON specs in
`logs/learning/hypotheses/`. Each spec is annotated with a verdict by
the hypothesis_runner (accepted / rejected / inconclusive). Until this
CLI ran, accepted specs just piled up with no terminal action -- the
self-learning loop had no last mile.

This module IS that last mile. It:

    1. Loads the spec by id.
    2. Validates it's accepted (refuses pending/rejected/inconclusive
       unless --force) and inside the TUNABLE_PARAMS whitelist.
    3. Refuses if the working tree is dirty (would mix unrelated
       changes into the auto-commit).
    4. Edits the targeted source file: `VAR = current_value` ->
       `VAR = proposed_value` exactly once.
    5. Validates the in-source value still matches the spec's
       current_value (drift detection — refuses if someone hand-edited
       since the hypothesis was generated).
    6. Marks the spec `promoted: true` with timestamp.
    7. Optionally git-commits the change.
    8. Optionally Pushovers the result.

Usage:
    python -m learning.promote --list
    python -m learning.promote <hyp_id>
    python -m learning.promote <hyp_id> --dry-run
    python -m learning.promote <hyp_id> --force --no-commit
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.hypothesis_engine import TUNABLE_PARAMS


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _hypotheses_dir() -> str:
    """Resolve lazily so monkeypatched config.LOG_DIR works in tests."""
    return os.path.join(config.LOG_DIR, "learning", "hypotheses")


def _spec_path(hyp_id: str) -> str:
    return os.path.join(_hypotheses_dir(), f"{hyp_id}.json")


# ── LOAD / LIST ──────────────────────────────────────

def load_spec(hyp_id: str) -> Optional[dict]:
    path = _spec_path(hyp_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"promote: failed to load {path}: {e}")
        return None


def list_accepted() -> list[dict]:
    """All accepted hypotheses that haven't been promoted yet."""
    d = _hypotheses_dir()
    if not os.path.isdir(d):
        return []
    out: list[dict] = []
    for path in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            with open(path) as f:
                spec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        verdict = (spec.get("verdict") or spec.get("status") or "").lower()
        if verdict == "accepted" and not spec.get("promoted"):
            out.append(spec)
    return out


# ── VALIDATION ────────────────────────────────────────

def validate_spec(spec: dict, force: bool = False) -> tuple[bool, str]:
    """
    Returns (ok, error_msg). `force=True` bypasses the accepted check
    but still enforces the whitelist + range guards.
    """
    verdict = (spec.get("verdict") or spec.get("status") or "").lower()
    if verdict != "accepted" and not force:
        return False, f"Not accepted (verdict={verdict!r}). Use --force to promote anyway."

    if spec.get("promoted"):
        return False, "Already promoted (re-running would be a no-op)"

    module = spec.get("module")
    var    = spec.get("var")
    if not module or not var:
        return False, "Spec missing module/var"

    rules = TUNABLE_PARAMS.get((module, var))
    if not rules:
        return False, f"({module}, {var}) not in TUNABLE_PARAMS whitelist"

    pv = spec.get("proposed_value")
    if pv is None:
        return False, "Spec missing proposed_value"

    try:
        pv_num = float(pv)
    except (TypeError, ValueError):
        return False, f"proposed_value {pv!r} is not numeric"

    if not (rules["min"] <= pv_num <= rules["max"]):
        return False, f"proposed_value {pv_num} outside [{rules['min']}, {rules['max']}]"

    return True, ""


# ── EDIT ──────────────────────────────────────────────

def module_to_path(module: str) -> str:
    """signals.regime_detector -> /…/signals/regime_detector.py"""
    return os.path.join(REPO_ROOT, *module.split(".")) + ".py"


def apply_edit(spec: dict) -> tuple[bool, str, str]:
    """
    Apply the var change to the source file.
    Returns (success, diff_summary, error_msg).
    """
    file_path = module_to_path(spec["module"])
    if not os.path.exists(file_path):
        return False, "", f"Source file not found: {file_path}"

    var      = spec["var"]
    current  = spec.get("current_value")
    proposed = spec["proposed_value"]

    # Match: "VAR = NUMBER" at module top level (possibly indented inside
    # a class, possibly with a trailing comment).
    pattern = re.compile(
        rf'^(\s*{re.escape(var)}\s*=\s*)([+\-]?\d+(?:\.\d+)?)(\s*(?:#.*)?)$',
        re.MULTILINE,
    )

    with open(file_path) as f:
        content = f.read()

    matches = pattern.findall(content)
    if not matches:
        return False, "", f"No '{var} = NUMBER' line found in {file_path}"
    if len(matches) > 1:
        return False, "", (
            f"Multiple '{var} = NUMBER' lines in {file_path} -- promote "
            f"would be ambiguous; hand-edit needed"
        )

    _, found_value, _ = matches[0]
    try:
        found_num = float(found_value)
    except ValueError:
        return False, "", f"Found value {found_value!r} is not numeric"

    if current is not None and abs(found_num - float(current)) > 0.001:
        return False, "", (
            f"Drift detected: source has {var}={found_num}, spec says "
            f"current_value={current}. File was edited since hypothesis "
            f"was generated -- review manually."
        )

    new_content = pattern.sub(rf"\g<1>{proposed}\g<3>", content, count=1)
    if new_content == content:
        return False, "", "Substitution produced no change (regex bug?)"

    with open(file_path, "w") as f:
        f.write(new_content)

    diff = f"{spec['module']}.{var}: {found_num} -> {proposed}"
    return True, diff, ""


def mark_promoted(spec: dict, spec_path: str) -> None:
    spec["promoted"]    = True
    spec["promoted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with open(spec_path, "w") as f:
            json.dump(spec, f, indent=2, default=str)
    except OSError as e:
        logger.warning(f"promote: failed to mark spec promoted: {e}")


# ── GIT ───────────────────────────────────────────────

def is_git_clean(cwd: str = REPO_ROOT) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True, cwd=cwd, capture_output=True, text=True,
        )
        return result.stdout.strip() == ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def git_commit(spec: dict, diff: str, cwd: str = REPO_ROOT) -> tuple[bool, str]:
    file_path = module_to_path(spec["module"])
    spec_path = _spec_path(spec["id"])
    sharpe_d  = spec.get("sharpe_delta")
    pnl_d     = spec.get("pnl_delta")
    sd_str    = f"{sharpe_d:+.3f}" if isinstance(sharpe_d, (int, float)) else "?"
    pd_str    = f"{pnl_d:+,.0f}"   if isinstance(pnl_d,    (int, float)) else "?"

    msg = (
        f"chore: promote hypothesis {spec.get('id')}\n\n"
        f"{diff}\n\n"
        f"Backtest deltas: sharpe {sd_str}, pnl {pd_str}\n"
        f"Rationale: {(spec.get('rationale') or '')[:200]}"
    )
    try:
        subprocess.run(["git", "add", file_path, spec_path],
                        check=True, cwd=cwd, capture_output=True)
        subprocess.run(["git", "commit", "-m", msg],
                        check=True, cwd=cwd, capture_output=True, text=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        out = (e.stderr or "").strip() if hasattr(e, "stderr") else str(e)
        return False, out


# ── MAIN PIPELINE ─────────────────────────────────────

def promote(
    hyp_id:      str,
    dry_run:     bool = False,
    force:       bool = False,
    no_commit:   bool = False,
    post_fn:     Callable[[str], None] | None = None,
) -> dict:
    """
    End-to-end promotion. Returns a result dict with `ok`, `error`,
    `diff`, etc. Never raises -- callers (CLI, scheduler hooks) should
    just inspect the dict.
    """
    spec = load_spec(hyp_id)
    if spec is None:
        return {"ok": False, "error": f"Spec not found: {hyp_id}"}

    ok, err = validate_spec(spec, force=force)
    if not ok:
        return {"ok": False, "error": err}

    if not no_commit and not is_git_clean():
        return {
            "ok": False,
            "error": "Working tree has uncommitted changes — commit/stash first, "
                     "or pass --no-commit to skip the git step.",
        }

    if dry_run:
        return {
            "ok":           True,
            "dry_run":      True,
            "would_edit":   module_to_path(spec["module"]),
            "would_change": f"{spec['var']}: {spec.get('current_value')} -> {spec['proposed_value']}",
        }

    success, diff, edit_err = apply_edit(spec)
    if not success:
        return {"ok": False, "error": edit_err}

    if not no_commit:
        ok_commit, commit_err = git_commit(spec, diff)
        if not ok_commit:
            return {
                "ok":   False,
                "error": f"Git commit failed: {commit_err}",
                "diff": diff,
                "note": "File was edited but not committed -- run `git diff` "
                        "to review.",
            }

    mark_promoted(spec, _spec_path(hyp_id))

    if post_fn:
        try:
            sharpe_d = spec.get("sharpe_delta")
            pnl_d    = spec.get("pnl_delta")
            sd_str   = f"{sharpe_d:+.2f}" if isinstance(sharpe_d, (int, float)) else "?"
            pd_str   = f"{pnl_d:+,.0f}"   if isinstance(pnl_d,    (int, float)) else "?"
            post_fn(
                f"**Hypothesis promoted: {hyp_id}**\n"
                f"{diff}\n"
                f"ΔSharpe {sd_str} · ΔP&L {pd_str}"
            )
        except Exception as e:
            logger.warning(f"promote: post_fn failed: {e}")

    return {"ok": True, "diff": diff, "spec_id": hyp_id}


# ── CLI ───────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog        = "python -m learning.promote",
        description = "Apply an accepted hypothesis to source code.",
    )
    parser.add_argument("hyp_id", nargs="?",
                        help="Hypothesis ID to promote (omit when using --list).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without editing or committing.")
    parser.add_argument("--force", action="store_true",
                        help="Promote even if verdict isn't 'accepted'.")
    parser.add_argument("--no-commit", action="store_true",
                        help="Edit the source file but skip the git commit.")
    parser.add_argument("--list", action="store_true",
                        help="List accepted, un-promoted hypotheses and exit.")
    args = parser.parse_args(argv)

    if args.list:
        items = list_accepted()
        if not items:
            print("No accepted, un-promoted hypotheses.")
            return 0
        print(f"{len(items)} accepted hypothesis(es) ready to promote:\n")
        for spec in items:
            sd = spec.get("sharpe_delta")
            pd = spec.get("pnl_delta")
            sd_s = f"{sd:+.2f}" if isinstance(sd, (int, float)) else "?"
            pd_s = f"{pd:+,.0f}" if isinstance(pd, (int, float)) else "?"
            print(f"  {spec['id']}")
            print(f"    {spec.get('module')}.{spec.get('var')}: "
                  f"{spec.get('current_value')} -> {spec.get('proposed_value')}")
            print(f"    ΔSharpe {sd_s} · ΔP&L {pd_s}")
            print(f"    {(spec.get('rationale') or '')[:100]}")
            print()
        return 0

    if not args.hyp_id:
        parser.error("hyp_id required (or use --list)")

    result = promote(
        hyp_id    = args.hyp_id,
        dry_run   = args.dry_run,
        force     = args.force,
        no_commit = args.no_commit,
    )
    if result.get("ok"):
        if result.get("dry_run"):
            print(f"[DRY-RUN] Would edit:   {result['would_edit']}")
            print(f"          Would change: {result['would_change']}")
        else:
            print(f"✅ Promoted: {result['diff']}")
            if not args.no_commit:
                print("           git commit landed.")
        return 0
    print(f"❌ {result.get('error')}")
    if result.get("diff"):
        print(f"   (file was edited: {result['diff']})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
