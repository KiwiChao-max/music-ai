# music-ai

AI-powered music processing app: upload an audio file, process it in a Celery
worker, then inspect separated stems, generated MIDI files and rule-based music
analysis in the web UI.

## What Works Now

- FastAPI backend with upload, task list/detail, processing, status, stems and analysis endpoints.
- Celery worker backed by Redis for long-running audio jobs.
- PostgreSQL schema managed by Alembic migrations.
- React/Vite frontend for uploading files, following progress and downloading outputs.
- Local fallback paths for development when Demucs or Basic Pitch cannot produce full-quality output.

## Architecture

- `frontend/` - React, Vite, TanStack Query, Tailwind CSS.
- `backend/` - FastAPI API, SQLAlchemy models, Alembic migrations and Celery worker.
- `scripts/` - local database bootstrap and smoke/e2e checks.
- `storage/` - ignored local uploads and generated artifacts.

Processing flow:

1. `POST /api/audio/upload` stores the audio under `storage/uploads/<task_id>/`.
2. `POST /api/tasks/{task_id}/process` queues a Celery job.
3. The worker writes stems, MIDI and `analysis.json` under `storage/outputs/task_<task_id>/`.
4. The frontend polls task status, then loads `/stems` and `/analysis`.

## Quick Start With Docker

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://127.0.0.1:8000`. The API container runs
`alembic upgrade head` on startup. To run the frontend locally against Docker:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## Local Development

Start infrastructure:

```bash
docker compose up -d postgres redis
```

Install backend dependencies and migrate the database:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
python scripts/init_db.py
```

Run the API and worker in separate terminals:

```bash
cd backend
uvicorn app.main:app --reload
```

```bash
cd backend
celery -A app.celery_app:celery worker --loglevel=info --concurrency=1
```

Run the frontend:

```bash
cd frontend
npm install
npm run dev
```

## Verification

With backend dependencies installed:

```bash
python -m compileall backend/app scripts
```

With frontend dependencies installed:

```bash
cd frontend
npm run build
```

Useful runtime checks:

```bash
python scripts/check_servers.py
python scripts/smoke_test.py
python scripts/e2e_tasks.py
python scripts/e2e_midi.py
```

The e2e scripts start their own API and worker on port `8000`, so close any
manual backend process first.

## Environment

Copy `.env.example` to `.env` and adjust as needed.

- `DATABASE_URL` overrides individual `DB_*` settings.
- `STORAGE_DIR` is the preferred single root for uploads and outputs.
- `UPLOAD_DIR` and `OUTPUT_DIR` can override the derived storage subfolders.
- `MAX_UPLOAD_BYTES` defaults to `209715200` (200 MB).
- `REDIS_URL` feeds Celery broker/result defaults.
- `CORS_ORIGINS` is a comma-separated list for browser clients.

## Current Gaps

- Demucs and Basic Pitch are heavy; first production-quality processing can be slow on CPU-only machines.
- There is no auth, quota system or per-user task ownership yet.
- Uploaded files are stored locally; production needs object storage or a managed persistent volume strategy.
- Analysis is deterministic and local; `llm_service.py` is still a placeholder for a future LLM commentary pass.
