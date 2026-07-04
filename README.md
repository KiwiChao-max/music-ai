# music-ai

AI-powered music processing app: upload an audio file, process it in a Celery
worker, then inspect separated stems, per-instrument MIDI files, GM-mapped
drum kits and rule-based music analysis in the web UI. The pipeline covers the
core audio-AI loop end-to-end (source separation → transcription → drum
splitting → GM/CC MIDI → user-supplied sample playback) and ships with the
five product-grade features described in [`FEATURES.md`](./FEATURES.md).

## What Works Now

- FastAPI backend with upload, task list/detail, processing, status, stems, analysis and sample-library endpoints.
- Celery worker backed by Redis for long-running audio jobs, with a 30-minute task time limit + soft-time-limit cleanup and a dead-letter queue for failed tasks.
- PostgreSQL schema managed by Alembic migrations; CI runs the test suite against real Postgres (not just SQLite).
- React/Vite frontend for uploading files, following progress, downloading outputs, browsing drum parts and managing sample libraries. Route-level code splitting keeps the initial bundle small.
- 6-stem Demucs separation (`htdemucs_6s`: vocals / drums / bass / other / piano / guitar), per-instrument Basic Pitch MIDI with full GM controllers (CC7/CC10/CC11/CC64/CC74/CC91/CC93, pitch bend), and a 19-part drum detector that emits per-part MIDI plus a JSON event list for the browser-side sample player.
- Velocity-layered sample libraries: filenames like `kick_pp.wav` / `snare_ff.wav` map to MIDI velocity ranges so the front-end picks the right sample per hit strength.
- A web-audio sample player that decodes user-uploaded drum samples and re-renders the detected hits through the active sample library.
- Local fallback paths for development when Demucs or Basic Pitch cannot produce full-quality output.
- **User accounts**: bcrypt + HS256 JWTs (access + refresh), per-user task ownership, per-user quotas (active tasks + upload bytes). Auth is **opt-in** via `AUTH_REQUIRED` so existing e2e keeps working; flip it on in any environment real users can reach.
- **Live progress over WebSocket**: `WS /api/ws/tasks/{id}/progress` publishes a `snapshot` then relays every `task:{id}` pub/sub message from the worker. The frontend patches the React Query cache in place — no more "wait 1.5 s for the next poll". Per-IP connection cap and ownership check enforced.
- **Health probes + metrics**: `GET /healthz` (liveness), `GET /readyz` (probes Postgres + Redis), `GET /metrics` (Prometheus exposition with a per-status task gauge).
- **LLM commentary**: `llm_service.py` ships a mock provider and an OpenAI-compatible provider, rendered in the UI as a `CommentaryCard`.
- **i18n + dark mode**: react-i18next (zh/en) with localStorage persistence; class-based dark mode with cross-tab sync.
- **Security hardening**: Redis-backed rate limiting on login/register/upload/task endpoints, zip-bomb protection, upload size limits, non-root Docker user, Docker compose resource limits (CPU + memory).
- **CI on every PR**: GitHub Actions runs the backend pytest suite against Postgres + Redis service containers, runs the frontend Vitest unit tests, type-checks + builds the frontend, and runs the Playwright e2e flow.
- **Production deployment docs**: see [`DEPLOY.md`](./DEPLOY.md) for the compose path and the bare-metal systemd + nginx path, plus a production hardening checklist.

## Architecture

- `frontend/` - React, Vite, TanStack Query, Tailwind CSS.
- `backend/` - FastAPI API, SQLAlchemy models, Alembic migrations and Celery worker.
- `scripts/` - local database bootstrap and smoke/e2e checks.
- `storage/` - ignored local uploads and generated artifacts.

Processing flow:

1. `POST /api/audio/upload` stores the audio under `storage/uploads/<task_id>/`.
2. `POST /api/tasks/{task_id}/process` queues a Celery job.
3. The worker:
   - runs Demucs → 6 stems (vocals / drums / bass / other / piano / guitar)
   - runs the instrument classifier on `other` → per-instrument stems
   - runs Basic Pitch → per-instrument MIDI with full GM controllers
   - runs the drum detector → 19 per-part MIDI files + `drums_events.json`
   - writes `analysis.json` (BPM, key, chords, sections, detected instruments)
4. The frontend polls task status, then loads `/stems`, `/analysis`, and (when an active sample library is set) decodes `drums_events.json` plus the library's samples through the Web Audio API.

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
cd backend
.venv/bin/pytest                      # 221 tests covering services, repos, MIDI, drum detection, sample library, auth, WebSocket, rate limiting, health
python -m compileall app scripts
```

With frontend dependencies installed:

```bash
cd frontend
npm run test                          # Vitest unit tests (upload util, ApiError)
npm run build                         # type-checks the whole TS tree
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
- `STORAGE_DIR` is the preferred single root for uploads, outputs and sample libraries.
- `UPLOAD_DIR` and `OUTPUT_DIR` can override the derived storage subfolders.
- `SAMPLE_LIBRARY_DIR` is the per-library upload root; defaults to `<STORAGE_DIR>/sample-libraries`.
- `MAX_UPLOAD_BYTES` defaults to `209715200` (200 MB). Sample library uploads are capped at 5 MB per file / 80 MB total.
- `REDIS_URL` feeds Celery broker/result defaults.
- `CORS_ORIGINS` is a comma-separated list for browser clients.

## API Highlights

- `GET  /api/audio/tasks` / `/api/audio/tasks/{id}` — task list & detail.
- `POST /api/audio/upload` — multipart upload.
- `POST /api/tasks/{id}/process` — enqueue the pipeline.
- `GET  /api/tasks/{id}/stems` / `/analysis` — outputs.
- `GET  /api/instruments/libraries` / `POST /api/instruments/libraries` / `POST /api/instruments/libraries/{id}/activate` — sample library CRUD (multi-file or zip upload, filename aliasing to GM notes).
- `GET  /api/instruments/active` — currently active library.
- `GET  /api/instruments/libraries/{id}/files/{note}` — fetch a single sample.

## Current Gaps

- Demucs and Basic Pitch are heavy; first production-quality processing can be slow on CPU-only machines.
- Uploaded files are stored locally; production needs object storage or a managed persistent volume strategy.
- Frontend test coverage is limited to pure utils and the API client layer; component-level tests are still TODO.

See [`FEATURES.md`](./FEATURES.md) for the full product-level feature breakdown.
