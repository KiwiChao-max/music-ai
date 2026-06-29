#!/usr/bin/env bash
# Check that everything the project needs to run locally is in place.
# Run this once before `setup_local.sh` to surface missing pieces.
#
# Exit code 0 = ready to set up. 1 = missing something; details above.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

# Always run direct (no system proxy); we hit GitHub / PyPI / brew directly.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY 2>/dev/null || true

PASS=0
FAIL=0
WARN=0

ok()   { printf "  \033[32m✓\033[0m  %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31m✗\033[0m  %s\n" "$1"; FAIL=$((FAIL+1)); }
warn() { printf "  \033[33m!\033[0m  %s\n" "$1"; WARN=$((WARN+1)); }
section() { printf "\n\033[1m== %s ==\033[0m\n" "$1"; }

section "System tools"
command -v brew      >/dev/null 2>&1 && ok "brew: $(brew --version | head -1)" || bad "brew not found (install from https://brew.sh)"
command -v git       >/dev/null 2>&1 && ok "git: $(git --version)"                || bad "git not found"
command -v node      >/dev/null 2>&1 && ok "node: $(node --version)"              || bad "node not found (need >= 18)"
command -v npm       >/dev/null 2>&1 && ok "npm: $(npm --version)"                || bad "npm not found"
command -v python3   >/dev/null 2>&1 && ok "python3: $(python3 --version)"        || bad "python3 not found"
command -v pip3      >/dev/null 2>&1 && ok "pip3: $(pip3 --version | head -1)"    || bad "pip3 not found"

section "Brew packages"
if command -v brew >/dev/null 2>&1; then
  for pkg in postgresql@16 redis ffmpeg; do
    if brew list "$pkg" >/dev/null 2>&1; then
      ok "$pkg installed"
    else
      bad "$pkg missing  →  run: brew install $pkg"
    fi
  done
fi

section "PostgreSQL"
if command -v pg_isready >/dev/null 2>&1; then
  if pg_isready -h localhost -p 5432 -q 2>/dev/null; then
    ok "PostgreSQL is accepting connections on localhost:5432"
  else
    bad "PostgreSQL is installed but not running"
    echo "      Hint: brew services start postgresql@16   (or run pg_ctl -D ... start)"
  fi
else
  warn "psql not on PATH yet (brew install postgresql@16 is still in progress or PATH not updated)"
fi

section "Redis"
if command -v redis-cli >/dev/null 2>&1; then
  if redis-cli -h localhost -p 6379 ping 2>/dev/null | grep -q PONG; then
    ok "Redis is responding to PING on localhost:6379"
  else
    bad "Redis is installed but not responding"
    echo "      Hint: brew services start redis"
  fi
else
  warn "redis-cli not on PATH yet"
fi

section "Python venv"
if [[ -d "$PROJECT_ROOT/backend/.venv" ]]; then
  ok "backend/.venv exists"
  if "$PROJECT_ROOT/backend/.venv/bin/python" -c "import fastapi, celery, sqlalchemy, alembic" 2>/dev/null; then
    ok "core backend packages import OK"
  else
    bad "core backend packages missing in venv → run scripts/local/setup_local.sh"
  fi
else
  warn "backend/.venv not created yet → run scripts/local/setup_local.sh"
fi

section "AI / audio (optional, large)"
# These are heavy; just report status, don't fail the check.
if python3 -c "import torch" 2>/dev/null; then
  ok "torch importable ($(python3 -c 'import torch; print(torch.__version__)'))"
else
  warn "torch not importable (Demucs will fall back to placeholder stems)"
fi
if python3 -c "import demucs" 2>/dev/null; then
  ok "demucs importable"
else
  warn "demucs not importable (will run with placeholders)"
fi
if python3 -c "import basic_pitch" 2>/dev/null; then
  ok "basic_pitch importable"
else
  warn "basic_pitch not importable (per-instrument MIDI will be skipped)"
fi
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
else
  warn "ffmpeg missing (compressed audio formats won't work)"
fi

section "Frontend"
if [[ -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  ok "frontend/node_modules installed"
else
  warn "frontend/node_modules missing → run: cd frontend && npm install"
fi

section "Config"
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  ok ".env exists"
else
  warn ".env missing (will be auto-created from .env.example by setup_local.sh)"
fi

section "Summary"
echo "  pass: $PASS   warn: $WARN   fail: $FAIL"
echo
if [[ $FAIL -gt 0 ]]; then
  echo "Ready: NO  ($FAIL blocking issue(s))"
  echo "Run:  ./scripts/local/setup_local.sh   to fix the installable ones."
  exit 1
fi
echo "Ready: YES  (warnings are non-blocking)"
