#!/usr/bin/env bash
# Stop all background services started by start_all_local.sh.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/scripts/local/logs"

stop_pid() {
  local name=$1 pidfile=$2
  if [[ -f "$pidfile" ]]; then
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      echo "  [stop] $name (pid=$pid)"
      kill "$pid" || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

stop_pid "api"     "$LOG_DIR/api.pid"
stop_pid "worker"  "$LOG_DIR/worker.pid"
stop_pid "web"     "$LOG_DIR/web.pid"

# Belt and suspenders: kill anything still bound to our ports.
for port in 8000 5173; do
  pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "  [cleanup] killing stragglers on port $port"
    kill $pids 2>/dev/null || true
  fi
done

echo "Done."
