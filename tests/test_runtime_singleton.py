"""runtime.singleton.acquire_or_die — process-level singleton lock.

Prevents a second main.py from running concurrently. The dual-process
incident on 2026-05-27 caused every Discord alert to post twice because
each instance had its own APScheduler and alert-dedup cache.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.singleton import acquire_or_die, SingletonLockError


def test_first_acquire_succeeds(tmp_path):
    lock_path = tmp_path / "test.lock"
    handle = acquire_or_die(str(lock_path))
    assert handle is not None
    assert lock_path.exists()


def test_second_acquire_in_same_process_raises(tmp_path):
    lock_path = tmp_path / "test.lock"
    _ = acquire_or_die(str(lock_path))  # hold the lock
    with pytest.raises(SingletonLockError):
        acquire_or_die(str(lock_path))


def test_lockfile_contains_pid(tmp_path):
    lock_path = tmp_path / "test.lock"
    acquire_or_die(str(lock_path))
    assert lock_path.read_text().strip() == str(os.getpid())


def test_error_message_includes_holder_pid(tmp_path):
    """Second acquire must surface the holder's PID, not pid= (empty)."""
    lock_path = tmp_path / "test.lock"
    acquire_or_die(str(lock_path))
    with pytest.raises(SingletonLockError) as exc:
        acquire_or_die(str(lock_path))
    assert f"pid={os.getpid()}" in str(exc.value)
