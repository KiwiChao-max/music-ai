#!/usr/bin/env bash
# One-shot local setup. Safe to re-run; each step is idempotent.
#
# What it does:
#   1. Installs system packages via Homebrew (postgresql@16, redis, ffmpeg)
#   2. Starts the services (brew services)
#   3. Creates the `music_ai` database
#   4. Creates a Python venv at backend/.venv
#   5. Installs backend/requirements.txt + requirements-dev.txt
#   6. Copies .env.example → .env if .env doesn't exist
#   7. Runs alembic upgrade head
#   8. Installs frontend dependencies
#
# After this finishes, run scripts/local/start_local.sh to actually launch the app.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

# Always run direct (no system proxy).
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY 2>/dev/null || true

# Configure pip to use the Tsinghua mirror (faster in CN and we already verified it works).
export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export PIP_TRUSTED_HOST="pypi.tuna.tsinghua.edu.cn"

step() { printf "\n\033[1m== %s ==\033[0m\n" "$1"; }

# ---------------------------------------------------------------------------
step "1. System packages (brew)"
# ---------------------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh first." >&2
  exit 1
fi
for pkg in postgresql@16 redis ffmpeg; do
  if brew list "$pkg" >/dev/null 2>&1; then
    echo "  [skip] $pkg already installed"
  else
    echo "  [install] $pkg ..."
    brew install "$pkg"
  fi
done

# ---------------------------------------------------------------------------
step "2. Start brew services"
# ---------------------------------------------------------------------------
brew services start postgresql@16 || true
brew services start redis         || true
sleep 2

# Make sure the postgres binary is on PATH for the rest of this script.
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"

# ---------------------------------------------------------------------------
step "3. Bootstrap PostgreSQL data dir (if needed)"
# ---------------------------------------------------------------------------
# Homebrew's first-run init may not have created the default cluster.
PG_DATA_DIR="$(brew --prefix)/var/postgresql@16"
if [[ ! -d "$PG_DATA_DIR" ]]; then
  echo "  [initdb] $PG_DATA_DIR"
  initdb --locale=C -E UTF-8 "$PG_DATA_DIR" >/dev/null
  brew services restart postgresql@16
  sleep 2
fi

# ---------------------------------------------------------------------------
step "4. Create database (postgres role + music_ai db)"
# ---------------------------------------------------------------------------
# Make sure the `postgres` role exists with the password the app expects.
# Homebrew's default install creates a role matching your macOS username, so
# we create the `postgres` role explicitly if it's missing.
if ! psql -U "$(whoami)" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='postgres'" 2>/dev/null | grep -q 1; then
  echo "  [create role] postgres / postgres123"
  psql -U "$(whoami)" -d postgres -c "CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD 'postgres123';" >/dev/null
else
  echo "  [ok] role 'postgres' already exists"
fi

# Allow local md5 password auth: edit pg_hba.conf only if it currently blocks us.
PG_HBA="$PG_DATA_DIR/pg_hba.conf"
if ! grep -q "host    all             all             127.0.0.1/32            md5" "$PG_HBA" 2>/dev/null; then
  echo "  [patch] pg_hba.conf to allow md5 over localhost"
  # Insert a 'host all all 127.0.0.1/32 md5' line just before the first 'host' entry
  # that doesn't already cover it. This is intentionally conservative.
  cp "$PG_HBA" "$PG_HBA.bak"
  awk '
    /^host/ && !done { print "host    all             all             127.0.0.1/32            md5"; done=1 }
    { print }
  ' "$PG_HBA.bak" > "$PG_HBA"
  brew services restart postgresql@16
  sleep 2
fi

# Create the music_ai database (idempotent via init_db.py's logic).
PGPASSWORD=postgres123 psql -h 127.0.0.1 -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='music_ai'" 2>/dev/null | grep -q 1 \
  || PGPASSWORD=postgres123 psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE music_ai;"
echo "  [ok] database 'music_ai' is ready"

# ---------------------------------------------------------------------------
step "5. Backend Python venv + dependencies"
# ---------------------------------------------------------------------------
cd "$PROJECT_ROOT/backend"
if [[ ! -d ".venv" ]]; then
  echo "  [create] .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools >/dev/null
echo "  [pip install] requirements.txt"
pip install -r requirements.txt
echo "  [pip install] requirements-dev.txt (test deps)"
pip install -r requirements-dev.txt

# ---------------------------------------------------------------------------
step "6. .env file"
# ---------------------------------------------------------------------------
cd "$PROJECT_ROOT"
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "  [create] .env from .env.example"
else
  echo "  [skip] .env already exists"
fi

# ---------------------------------------------------------------------------
step "7. Alembic migrations"
# ---------------------------------------------------------------------------
cd "$PROJECT_ROOT/backend"
alembic upgrade head
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
step "8. Frontend dependencies"
# ---------------------------------------------------------------------------
if [[ ! -d "frontend/node_modules" ]]; then
  echo "  [npm install] (this may take a minute) ..."
  (cd frontend && npm install)
else
  echo "  [skip] frontend/node_modules already installed"
fi

# ---------------------------------------------------------------------------
step "Done"
# ---------------------------------------------------------------------------
echo
echo "Setup complete. Next:"
echo "  ./scripts/local/check_env.sh   # verify everything is green"
echo "  ./scripts/local/start_local.sh # launch API + worker + frontend"
