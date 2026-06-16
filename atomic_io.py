"""atomic_io.py -- crash/freeze-safe file writes.

A naive ``open(path, "w")`` truncates the file to empty BEFORE writing, so any
interruption between the truncate and the write completing — a freeze (memory
pressure), crash, OOM kill, or power loss — leaves the file empty or corrupt.
That is exactly how ``logs/spy_daily_plans.json`` got wiped to 0 bytes on
2026-06-15 when the bot froze mid-write.

``atomic_write_text`` writes to a temp file in the SAME directory, flushes +
fsyncs it to disk, then ``os.replace()``s it onto the target. ``os.replace`` is
atomic on a single filesystem, so a reader (or a crash) can only ever observe
the *old* complete file or the *new* complete file — never a half-written one.
The temp file is in the same dir so the rename stays on one filesystem.
"""
from __future__ import annotations

import os
import tempfile


def atomic_write_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write `text` to `path`. Never leaves the target truncated:
    on any failure the original file is untouched and the temp file is removed."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".swap")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())          # durable before the rename
        os.replace(tmp, path)             # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp)                # don't leak the temp file
        except OSError:
            pass
        raise
