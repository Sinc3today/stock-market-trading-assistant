"""
tests/test_learning_promote.py -- promote pipeline coverage.

Each test isolates LOG_DIR to tmp_path and uses --no-commit to avoid
hitting real git inside the test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import promote as p


@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


@pytest.fixture
def fake_source(tmp_path, monkeypatch):
    """
    Stand up a fake `signals/regime_detector.py` under tmp_path so the
    promote module's apply_edit has something to write to without touching
    the real source tree.
    """
    src_dir = tmp_path / "src"
    (src_dir / "signals").mkdir(parents=True)
    src_file = src_dir / "signals" / "regime_detector.py"
    src_file.write_text(
        "# regime detector\n"
        "ADX_TREND_MIN = 25.0   # tuned threshold\n"
        "VIX_CALM_MAX  = 17.0\n"
        "OTHER         = 'no-touch'\n"
    )
    monkeypatch.setattr(p, "REPO_ROOT", str(src_dir))
    return src_file


def _write_spec(iso_logs, **overrides) -> tuple[str, dict]:
    """Helper: write a default valid spec, return (hyp_id, dict)."""
    spec = {
        "id":             "hyp_test_a",
        "date":           "2026-05-23",
        "title":          "raise ADX threshold",
        "rationale":      "raise threshold to filter weak trends",
        "module":         "signals.regime_detector",
        "var":            "ADX_TREND_MIN",
        "current_value":  25.0,
        "proposed_value": 27.0,
        "confidence":     0.6,
        "verdict":        "accepted",
        "sharpe_delta":   0.15,
        "pnl_delta":      350.0,
    }
    spec.update(overrides)
    d = Path(iso_logs) / "learning" / "hypotheses"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{spec['id']}.json"
    path.write_text(json.dumps(spec))
    return spec["id"], spec


# ─────────────────────────────────────────
# load + list
# ─────────────────────────────────────────

def test_load_spec_missing(iso_logs):
    assert p.load_spec("does_not_exist") is None


def test_list_accepted_groups_correctly(iso_logs):
    _write_spec(iso_logs, id="hyp_a", verdict="accepted")
    _write_spec(iso_logs, id="hyp_b", verdict="rejected")
    _write_spec(iso_logs, id="hyp_c", verdict="accepted", promoted=True)
    out = p.list_accepted()
    ids = [s["id"] for s in out]
    assert ids == ["hyp_a"]   # rejected + already-promoted excluded


# ─────────────────────────────────────────
# validate_spec
# ─────────────────────────────────────────

def test_validate_accepts_clean_spec(iso_logs):
    _, spec = _write_spec(iso_logs)
    ok, err = p.validate_spec(spec)
    assert ok and err == ""


def test_validate_rejects_non_accepted(iso_logs):
    _, spec = _write_spec(iso_logs, verdict="rejected")
    ok, err = p.validate_spec(spec)
    assert not ok and "Not accepted" in err


def test_validate_force_allows_non_accepted(iso_logs):
    _, spec = _write_spec(iso_logs, verdict="inconclusive")
    ok, err = p.validate_spec(spec, force=True)
    assert ok


def test_validate_rejects_already_promoted(iso_logs):
    _, spec = _write_spec(iso_logs, promoted=True)
    ok, err = p.validate_spec(spec)
    assert not ok and "Already promoted" in err


def test_validate_rejects_off_whitelist_var(iso_logs):
    _, spec = _write_spec(iso_logs, module="config", var="NOT_A_REAL_VAR")
    ok, err = p.validate_spec(spec)
    assert not ok and "whitelist" in err


def test_validate_rejects_out_of_range_value(iso_logs):
    _, spec = _write_spec(iso_logs, proposed_value=99.0)   # well above ADX max
    ok, err = p.validate_spec(spec)
    assert not ok and "outside" in err


# ─────────────────────────────────────────
# apply_edit
# ─────────────────────────────────────────

def test_apply_edit_happy_path(iso_logs, fake_source):
    _, spec = _write_spec(iso_logs)
    success, diff, err = p.apply_edit(spec)
    assert success
    assert "ADX_TREND_MIN: 25.0 -> 27.0" in diff
    body = fake_source.read_text()
    assert "ADX_TREND_MIN = 27.0" in body
    # Untouched lines stay
    assert "VIX_CALM_MAX  = 17.0" in body
    assert "OTHER         = 'no-touch'" in body


def test_apply_edit_detects_value_drift(iso_logs, fake_source):
    """If source has been hand-edited since the hypothesis was generated,
    refuse to overwrite -- could destroy the manual change."""
    _, spec = _write_spec(iso_logs, current_value=20.0)   # spec says 20, source says 25
    success, diff, err = p.apply_edit(spec)
    assert not success
    assert "Drift" in err


def test_apply_edit_missing_var_line(iso_logs, fake_source):
    _, spec = _write_spec(iso_logs, var="UNDEFINED_THRESHOLD",
                                     module="signals.regime_detector")
    success, diff, err = p.apply_edit(spec)
    assert not success
    assert "No 'UNDEFINED_THRESHOLD" in err


def test_apply_edit_missing_source_file(iso_logs, fake_source):
    _, spec = _write_spec(iso_logs, module="signals.does_not_exist")
    success, diff, err = p.apply_edit(spec)
    assert not success
    assert "not found" in err


# ─────────────────────────────────────────
# promote() pipeline
# ─────────────────────────────────────────

def test_promote_dry_run_makes_no_changes(iso_logs, fake_source, monkeypatch):
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    _, spec = _write_spec(iso_logs)
    result = p.promote("hyp_test_a", dry_run=True)
    assert result["ok"] and result["dry_run"]
    # Source file untouched
    assert "ADX_TREND_MIN = 25.0" in fake_source.read_text()
    # Spec NOT marked promoted
    reloaded = p.load_spec("hyp_test_a")
    assert not reloaded.get("promoted")


def test_promote_full_path_marks_promoted(iso_logs, fake_source, monkeypatch):
    """--no-commit + clean git -> edit + mark promoted, no git call."""
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    _, spec = _write_spec(iso_logs)
    result = p.promote("hyp_test_a", no_commit=True)
    assert result["ok"]
    assert "ADX_TREND_MIN: 25.0 -> 27.0" in result["diff"]
    # Source updated
    assert "ADX_TREND_MIN = 27.0" in fake_source.read_text()
    # Spec marked
    reloaded = p.load_spec("hyp_test_a")
    assert reloaded["promoted"] is True
    assert "promoted_at" in reloaded


def test_promote_refuses_dirty_git(iso_logs, fake_source, monkeypatch):
    """When the working tree is dirty AND we'd commit, refuse."""
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: False)
    _write_spec(iso_logs)
    result = p.promote("hyp_test_a")
    assert not result["ok"]
    assert "uncommitted changes" in result["error"].lower()


def test_promote_skips_git_check_with_no_commit(iso_logs, fake_source, monkeypatch):
    """--no-commit means we don't need a clean tree."""
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: False)
    _write_spec(iso_logs)
    result = p.promote("hyp_test_a", no_commit=True)
    assert result["ok"]


def test_promote_calls_post_fn_on_success(iso_logs, fake_source, monkeypatch):
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    _write_spec(iso_logs)
    captured: list[str] = []
    result = p.promote("hyp_test_a", no_commit=True,
                        post_fn=lambda m: captured.append(m))
    assert result["ok"]
    assert len(captured) == 1
    assert "promoted" in captured[0].lower()
    assert "hyp_test_a" in captured[0]


def test_promote_missing_spec_returns_error(iso_logs):
    result = p.promote("does_not_exist", no_commit=True)
    assert not result["ok"]
    assert "not found" in result["error"].lower()


# ─────────────────────────────────────────
# CLI
# ─────────────────────────────────────────

def test_cli_list_with_no_hypotheses(iso_logs, capsys):
    exit_code = p.main(["--list"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No accepted" in out


def test_cli_list_shows_accepted(iso_logs, capsys):
    _write_spec(iso_logs)
    exit_code = p.main(["--list"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "hyp_test_a" in out
    assert "ADX_TREND_MIN" in out


def test_cli_dry_run_prints_preview(iso_logs, fake_source, capsys, monkeypatch):
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    _write_spec(iso_logs)
    exit_code = p.main(["hyp_test_a", "--dry-run"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "DRY-RUN" in out
    assert "Would change" in out


def test_cli_promote_error_exits_nonzero(iso_logs, capsys):
    exit_code = p.main(["does_not_exist", "--no-commit"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "❌" in out


# ─────────────────────────────────────────
# SAFETY REVIEW
# Tests below cover failure modes a hostile or malformed spec could hit.
# ─────────────────────────────────────────

def test_validate_rejects_module_path_injection(iso_logs):
    """A spec naming a module outside TUNABLE_PARAMS is rejected by the
    whitelist check, regardless of whether the path resolves to anything
    real on disk."""
    _, spec = _write_spec(iso_logs, module="../../etc/passwd",
                          var="ADX_TREND_MIN")
    ok, err = p.validate_spec(spec)
    assert not ok
    assert "TUNABLE_PARAMS" in err


def test_validate_rejects_var_name_with_special_chars(iso_logs):
    """A var name that's not on the whitelist is rejected, even if it
    happens to match an existing variable in the source."""
    _, spec = _write_spec(iso_logs, var="VIX_CALM_MAX; os.system('rm')")
    ok, err = p.validate_spec(spec)
    assert not ok
    assert "TUNABLE_PARAMS" in err


def test_list_accepted_skips_corrupt_json(iso_logs):
    """A corrupt .json in the hypotheses dir doesn't crash list_accepted."""
    _write_spec(iso_logs, id="hyp_good", verdict="accepted")
    bad = Path(iso_logs) / "learning" / "hypotheses" / "hyp_bad.json"
    bad.write_text("{this is not valid json")
    out = p.list_accepted()
    assert [s["id"] for s in out] == ["hyp_good"]


def test_apply_edit_refuses_when_multiple_matches(iso_logs, tmp_path, monkeypatch):
    """Two `VAR = NUMBER` lines in the file → ambiguous, refuse rather
    than silently picking one."""
    src_dir = tmp_path / "src2"
    (src_dir / "signals").mkdir(parents=True)
    src_file = src_dir / "signals" / "regime_detector.py"
    src_file.write_text(
        "ADX_TREND_MIN = 25.0\n"
        "# second definition (this should never happen in real code)\n"
        "ADX_TREND_MIN = 30.0\n"
    )
    monkeypatch.setattr(p, "REPO_ROOT", str(src_dir))
    _, spec = _write_spec(iso_logs)
    ok, _, err = p.apply_edit(spec)
    assert not ok
    assert "Multiple" in err
    # Source untouched
    assert "ADX_TREND_MIN = 25.0" in src_file.read_text()
    assert "ADX_TREND_MIN = 30.0" in src_file.read_text()


def test_promote_force_allows_non_accepted_end_to_end(iso_logs, fake_source, monkeypatch):
    """--force lets an inconclusive verdict promote end-to-end (not just
    pass validate_spec)."""
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    _write_spec(iso_logs, verdict="inconclusive")
    result = p.promote("hyp_test_a", force=True, no_commit=True)
    assert result["ok"]
    assert "ADX_TREND_MIN = 27.0" in fake_source.read_text()


def test_promote_git_commit_failure_leaves_edit_visible(iso_logs, fake_source, monkeypatch):
    """If git_commit fails, the source file IS modified but the spec is
    NOT marked promoted — so the user can `git diff` and decide."""
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    monkeypatch.setattr(p, "git_commit",
                        lambda spec, diff, cwd=None: (False, "fatal: index lock"))
    _write_spec(iso_logs)
    result = p.promote("hyp_test_a")
    assert not result["ok"]
    assert "Git commit failed" in result["error"]
    # File edit went through
    assert "ADX_TREND_MIN = 27.0" in fake_source.read_text()
    # Half-state diff is surfaced + a helpful note
    assert result.get("diff")
    assert "not committed" in result.get("note", "")
    # Spec NOT marked promoted (so re-running after fixing git works)
    reloaded = p.load_spec("hyp_test_a")
    assert not reloaded.get("promoted")


def test_promote_idempotent_after_success(iso_logs, fake_source, monkeypatch):
    """Re-running promote on an already-promoted spec is a clean refusal,
    not a double-edit."""
    monkeypatch.setattr(p, "is_git_clean", lambda *a, **k: True)
    _write_spec(iso_logs)
    first = p.promote("hyp_test_a", no_commit=True)
    assert first["ok"]
    # Second run: validate_spec catches `promoted: true`
    second = p.promote("hyp_test_a", no_commit=True)
    assert not second["ok"]
    assert "Already promoted" in second["error"]
    # Source value unchanged from the first promote (no second substitution)
    assert fake_source.read_text().count("ADX_TREND_MIN = 27.0") == 1


def test_apply_edit_rejects_non_numeric_found_value(iso_logs, tmp_path, monkeypatch):
    """If somehow the source line says `ADX_TREND_MIN = 'foo'`, the
    regex won't match and we get a clean 'No line found' error rather
    than crashing on float() conversion."""
    src_dir = tmp_path / "src3"
    (src_dir / "signals").mkdir(parents=True)
    src_file = src_dir / "signals" / "regime_detector.py"
    src_file.write_text("ADX_TREND_MIN = 'not_a_number'\n")
    monkeypatch.setattr(p, "REPO_ROOT", str(src_dir))
    _, spec = _write_spec(iso_logs)
    ok, _, err = p.apply_edit(spec)
    assert not ok
    assert "No 'ADX_TREND_MIN" in err
