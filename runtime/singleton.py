"""Process-level singleton lock for main.py.

Uses fcntl.flock with LOCK_EX | LOCK_NB. The returned file handle MUST be
kept alive for the lifetime of the process — closing the fd releases the
lock. Stashing it on a module global is the simplest way to do that.
"""

import fcntl
import os


class SingletonLockError(RuntimeError):
    pass


_HELD_HANDLES: list = []


def acquire_or_die(lock_path: str):
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    # O_RDWR|O_CREAT — never truncate at open, so a failing acquire can still
    # read the holder's PID for the error message.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    handle = os.fdopen(fd, "r+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        handle.seek(0)
        existing_pid = handle.read().strip() or "unknown"
        handle.close()
        raise SingletonLockError(
            f"another instance is already running (lock={lock_path}, pid={existing_pid})"
        ) from e

    handle.seek(0)
    handle.truncate(0)
    handle.write(str(os.getpid()))
    handle.flush()
    _HELD_HANDLES.append(handle)
    return handle
