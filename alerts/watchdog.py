"""alerts/watchdog.py -- liveness watchdog.

After the 2026-06-15 SILENT freeze (the bot hung for the morning, never crashed,
so nothing alerted and Restart= didn't fire), two complementary mechanisms:

  1. AUTO-RESTART: `ping()` runs on a timer and sends systemd's WATCHDOG=1. If the
     bot freezes the pings stop, and with WatchdogSec= in the unit systemd kills +
     restarts it. A frozen process can't ping, which is exactly the point.
  2. RECOVERY ALERT: each ping also stamps a heartbeat file. On startup
     `check_recovery()` compares the last stamp to now and fires an EMERGENCY
     Pushover if the gap is large — so a freeze/restart is never silent again.

Tuning (main.py / unit): ping every 60s, WatchdogSec=300 (5x margin against false
restarts), recovery threshold 240s (> a clean restart's seconds-long gap, < the
watchdog's 5-min gap, so freeze-restarts alert but normal deploys don't).
"""
from __future__ import annotations

import os
import socket
import time

from loguru import logger

STALE_THRESHOLD_S = 240   # gap > this on startup = we were down -> alert


def _heartbeat_path() -> str:
    import config
    return os.path.join(config.LOG_DIR, "watchdog_heartbeat.txt")


def sd_notify(state: str) -> None:
    """Best-effort systemd sd_notify. No-op when not running under systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):          # abstract namespace socket
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode())
    except OSError as e:
        logger.debug(f"sd_notify failed: {e}")


def stamp_heartbeat(path: str | None = None, now: float | None = None) -> None:
    from atomic_io import atomic_write_text
    atomic_write_text(path or _heartbeat_path(),
                      str(now if now is not None else time.time()))


def ping(path: str | None = None) -> None:
    """Timer callback: tell systemd we're alive + stamp the heartbeat file.
    If the process is frozen this never runs -> systemd restarts it."""
    sd_notify("WATCHDOG=1")
    stamp_heartbeat(path)


def last_heartbeat(path: str | None = None) -> float | None:
    try:
        return float(open(path or _heartbeat_path()).read().strip())
    except (OSError, ValueError):
        return None


def gap_seconds(last_ts: float | None, now: float | None = None) -> float | None:
    if last_ts is None:
        return None
    return (now if now is not None else time.time()) - last_ts


def recovery_alert_message(gap_s: float) -> str:
    mins = int(gap_s // 60)
    return (f"⚠️ Trading bot recovered after a {mins}-min gap — possible freeze or "
            f"restart. Verify today's morning play + prediction were captured.")


def check_recovery(pushover, path: str | None = None, now: float | None = None,
                   threshold_s: int = STALE_THRESHOLD_S) -> bool:
    """On startup: if the last heartbeat is older than `threshold_s`, fire an
    emergency Pushover. Returns True iff an alert was sent. Silent on first-ever
    boot (no prior heartbeat) so it never false-alarms."""
    gap = gap_seconds(last_heartbeat(path), now)
    if gap is not None and gap > threshold_s:
        logger.warning(f"Watchdog: {int(gap // 60)}-min heartbeat gap on startup — alerting")
        if pushover:
            pushover.send("⚠️ Bot recovered", recovery_alert_message(gap), priority=2)
        return True
    return False
