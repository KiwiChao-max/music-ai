"""Initialize the local music_ai database.

Database connection
-------------------
    Override via environment variables: DB_HOST, DB_PORT, DB_USER,
    DB_PASSWORD, DB_NAME. Defaults match the docker-compose dev setup.

Usage
-----
    # 1) install dependency (one-time)
    pip install -r backend/requirements.txt

    # 2) run from the project root
    python scripts/init_db.py

The script is idempotent: running it multiple times is safe. It will:
    1. create the `music_ai` database if it does not exist
    2. invoke `alembic upgrade head` to apply any pending migrations
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import psycopg2.extensions
from psycopg2 import sql

# ---- connection settings ---------------------------------------------------
# Override via environment variables; defaults match docker-compose dev setup.
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres123")
DB_NAME = os.getenv("DB_NAME", "music_ai")

# ---- paths -----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"


def _admin_connect():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname="postgres",
    )


def ensure_database() -> None:
    conn = _admin_connect()
    try:
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(DB_NAME)))
                print(f"[ok] database created: {DB_NAME}")
            else:
                print(f"[skip] database already exists: {DB_NAME}")
    finally:
        conn.close()


def run_alembic_upgrade() -> None:
    env = os.environ.copy()
    env.setdefault("DB_HOST", DB_HOST)
    env.setdefault("DB_PORT", str(DB_PORT))
    env.setdefault("DB_USER", DB_USER)
    env.setdefault("DB_PASSWORD", DB_PASSWORD)
    env.setdefault("DB_NAME", DB_NAME)

    print("[run] alembic upgrade head")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        text=True,
    )
    if result.returncode != 0:
        print("[fail] alembic upgrade failed", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> int:
    print(
        f"connecting to postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/  target={DB_NAME}"
    )
    ensure_database()
    run_alembic_upgrade()
    print("[done] schema is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
