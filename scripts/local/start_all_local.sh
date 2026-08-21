#!/usr/bin/env bash
# Background all three services and write logs under scripts/local/logs/.
# Use stop_all_local.sh to bring them down.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/scripts/local/logs"
mkdir -p "$LOG_DIR"

# Warn (but do not exit) if a port is already in use; the app is happy to
# share Postgres and Redis with the host's existing services.
check_port() {
  local port=$1 name=$2
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  [ok] port $port already serving $name (will reuse)"
    return 0
  fi
}
check_port 8000 "api"
check_port 5173 "vite"
check_port 6379 "redis"
check_port 5432 "postgres"

# ---- API ------------------------------------------------------------------
cd "$PROJECT_ROOT/backend"
source .venv/bin/activate
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > "$LOG_DIR/api.log" 2>&1 &
echo $! > "$LOG_DIR/api.pid"
echo "  [start] api  pid=$(cat "$LOG_DIR/api.pid")  log=$LOG_DIR/api.log"

# ---- Celery worker --------------------------------------------------------
# OBJC_DISABLE_INITIALIZE_FORK_SAFETY: macOS only. Celery's prefork pool calls
# fork(); if a threaded audio lib (soundfile/torch) is mid-ObjC init at that
# moment the child dies with SIGABRT and audio tasks hang forever. Disabling the
# fork safety check is the standard workaround on macOS.
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES nohup celery -A app.celery_app:celery worker --loglevel=info --concurrency=2 \
  > "$LOG_DIR/worker.log" 2>&1 &
echo $! > "$LOG_DIR/worker.pid"
echo "  [start] worker  pid=$(cat "$LOG_DIR/worker.pid")  log=$LOG_DIR/worker.log"

# ---- Frontend -------------------------------------------------------------
cd "$PROJECT_ROOT/frontend"
nohup npm run dev -- --host 0.0.0.0 --port 5173 \
  > "$LOG_DIR/web.log" 2>&1 &
echo $! > "$LOG_DIR/web.pid"
echo "  [start] web  pid=$(cat "$LOG_DIR/web.pid")  log=$LOG_DIR/web.log"

cat <<EOF

Started. Give it ~5 seconds, then:
  curl -s http://127.0.0.1:8000/health          # backend health
  open  http://127.0.0.1:5173                    # frontend

To tail logs:
  tail -f $LOG_DIR/api.log
  tail -f $LOG_DIR/worker.log
  tail -f $LOG_DIR/web.log

To stop:
  ./scripts/local/stop_all_local.sh
EOF
