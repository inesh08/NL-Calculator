#!/usr/bin/env bash
# Runs the Slack web app (gunicorn, ONE worker) and the health monitor together.
# If either process exits for any reason the container exits non-zero, so
# Cloudflare sees a stopped container and the Worker restarts it on the next
# request. No silent half-alive state.
set -u

PORT="${PORT:-8080}"

# ONE worker on purpose: event de-duplication and pending clarifications live in
# this process's memory, and one thread drains the event queue. Threads are fine.
# Do NOT add --preload: the Zoroval SDK initialises at import time and skips its
# exit flush in forked children, so queued traces would be lost on shutdown.
# Without preload, init runs inside the worker and gunicorn's graceful SIGTERM
# exit flushes (--graceful-timeout leaves room for that; Cloudflare waits up to
# 15 minutes after SIGTERM before SIGKILL).
gunicorn slack_app:app \
  --workers 1 --threads 4 \
  --bind "0.0.0.0:${PORT}" \
  --timeout 60 --graceful-timeout 20 \
  --access-logfile - &
web_pid=$!

python monitor.py &
monitor_pid=$!

stop_children() {
  kill -TERM "$web_pid" "$monitor_pid" 2>/dev/null
  wait "$web_pid" "$monitor_pid" 2>/dev/null
}

# Platform shutdown (SIGTERM): stop both children, exit cleanly.
trap 'stop_children; exit 0' TERM INT

wait -n "$web_pid" "$monitor_pid"
code=$?
echo "entrypoint: a child process exited (code ${code}); stopping container" >&2
stop_children
# Never exit 0 here: an unexpected exit is always a failure.
if [ "$code" -eq 0 ]; then code=1; fi
exit "$code"
