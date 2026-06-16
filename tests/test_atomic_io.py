"""tests/test_atomic_io.py -- crash/freeze-safe atomic file writes.

Regression context: a naive open(path,"w") truncates before writing, so an
interruption leaves an empty/corrupt file (this wiped spy_daily_plans.json on
2026-06-15). atomic_write_text must never leave the target half-written.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from atomic_io import atomic_write_text


def test_writes_content(tmp_path):
    p = tmp_path / "out.json"
    atomic_write_text(str(p), '{"a": 1}')
    assert p.read_text() == '{"a": 1}'


def test_overwrites_existing(tmp_path):
    p = tmp_path / "out.txt"
    p.write_text("OLD CONTENT")
    atomic_write_text(str(p), "NEW")
    assert p.read_text() == "NEW"


def test_no_temp_files_left_behind(tmp_path):
    p = tmp_path / "out.txt"
    atomic_write_text(str(p), "data")
    leftovers = [f for f in os.listdir(tmp_path) if f != "out.txt"]
    assert leftovers == []          # no .tmp-* swap files leak


def test_creates_missing_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deep" / "out.txt"
    atomic_write_text(str(p), "x")
    assert p.read_text() == "x"


def test_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    # if the rename fails mid-write, the original file must survive intact
    # and no temp file should be left behind.
    p = tmp_path / "out.txt"
    p.write_text("ORIGINAL")
    import atomic_io
    monkeypatch.setattr(atomic_io.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_text(str(p), "NEW")
    assert p.read_text() == "ORIGINAL"                       # untouched
    assert [f for f in os.listdir(tmp_path) if f != "out.txt"] == []  # temp cleaned
