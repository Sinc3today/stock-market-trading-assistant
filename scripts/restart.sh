#!/usr/bin/env bash
# scripts/restart.sh — Kill the running main.py + uvicorn child and
# relaunch from a fresh git checkout. Run after `git pull` to roll new
# code into the live dashboard.
#
# Usage:  ./scripts/restart.sh
#
# Behavior:
#   1. Find any python process running `main.py` (the entry point that
#      spawns the uvicorn child).
#   2. Send SIGTERM. Wait up to 5s for graceful shutdown.
#   3. SIGKILL anything still alive.
#   4. Relaunch main.py with nohup, log to /tmp/smta_main.log, print
#      the new PID + a health-check curl.
#
# Safe to run repeatedly. Does nothing destructive — no git state changes,
# no DB writes, no file deletion.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="/tmp/smta_main.log"
HEALTH_URL="http://127.0.0.1:8002/health"
WAIT_SECONDS=5

cd "$REPO_ROOT"

echo "▶ Looking for running main.py / uvicorn …"
PIDS=$(pgrep -f "python.*main\.py|uvicorn alerts\.web_app" || true)
if [[ -n "$PIDS" ]]; then
  echo "  Found: $PIDS"
  echo "  Sending SIGTERM …"
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null || true

  # Wait up to WAIT_SECONDS for graceful exit
  for i in $(seq 1 $WAIT_SECONDS); do
    sleep 1
    STILL=$(pgrep -f "python.*main\.py|uvicorn alerts\.web_app" || true)
    [[ -z "$STILL" ]] && break
  done

  STILL=$(pgrep -f "python.*main\.py|uvicorn alerts\.web_app" || true)
  if [[ -n "$STILL" ]]; then
    echo "  Forcing SIGKILL on stragglers: $STILL"
    # shellcheck disable=SC2086
    kill -9 $STILL 2>/dev/null || true
    sleep 1
  fi
else
  echo "  Nothing running."
fi

echo "▶ Launching main.py …"
nohup .venv/bin/python main.py > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "  Started: PID $NEW_PID  (log: $LOG_FILE)"

echo "▶ Waiting for /health …"
for i in $(seq 1 10); do
  sleep 1
  if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "  ✓ $HEALTH_URL is responding (took ${i}s)"
    exit 0
  fi
done

echo "  ✗ /health didn't respond after 10s — check $LOG_FILE"
tail -n 20 "$LOG_FILE"
exit 1
