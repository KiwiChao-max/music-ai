#!/usr/bin/env bash
# Launch the three dev services in the foreground (one per terminal, ideally).
# This script just prints the three commands to run; copy/paste into your
# terminals OR use start_all_local.sh to background everything.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

cat <<EOF
Three processes. Run each in its own terminal tab:

  [1] Backend API    →  cd $PROJECT_ROOT/backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
  [2] Celery worker  →  cd $PROJECT_ROOT/backend && source .venv/bin/activate && celery -A app.celery_app:celery worker --loglevel=info --concurrency=2
  [3] Frontend       →  cd $PROJECT_ROOT/frontend && npm run dev

Or, to background them all and log to scripts/local/logs/:
  ./scripts/local/start_all_local.sh
EOF
