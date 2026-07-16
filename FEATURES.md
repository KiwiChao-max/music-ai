# FEATURES

The product targets five audio-AI capabilities end-to-end. Each feature is
backed by a backend service, a REST endpoint, a database migration (where
persistent) and a frontend surface. Tests live under
`backend/tests/`.

---

## 1. Instrument-level MIDI transcription

> "Split the drums / bass / guitar / piano / strings / other into editable
> MIDI."

- **Demucs** (`app/services/demucs_service.py`) splits the master into 6
  stems using the `htdemucs_6s` model: `vocals`, `drums`, `bass`, `piano`,
  `guitar`, `other`. Piano and guitar are first-class stems (no longer
  derived from `other` by the rule-based classifier).
- The **instrument classifier** (`app/services/instrument_classifier_service.py`)
  runs on the `other` stem and produces per-instrument WAVs for
  `other_piano`, `other_guitar`, `other_strings`, `other_synth`, and a
  residual `other_other_melodic` bucket. Classification uses per-frame
  spectral features (centroid, bandwidth, rolloff, flatness, ZCR, HF /
  low-band ratios) against a small heuristic rule table --- no external
  model dependency.
- **Spotify Basic Pitch** (`app/services/basic_pitch_service.py`) transcribes
  each melodic stem into a polyphonic MIDI file. Each output is
  prepended with a full GM setup sequence (see Feature 4). The
  `_normalize_stem_key` helper strips the `other_` prefix from
  classifier-produced files so `other_strings.mid` picks up the String
  Ensemble voice (program 48) instead of falling through to Warm Pad.
- The detector writes a `detected_instruments` block (per-instrument
  probability + dominant label) to `analysis.json`, which the frontend
  renders on the task detail page as a sorted list with a probability
  progress bar.

API: outputs are listed under `GET /api/tasks/{id}/stems` and
`GET /api/tasks/{id}/analysis`.

---

## 2. 19-part drum split

> "The drum MIDI can be further split into kick / snare / closed & open
> hi-hats / cymbals / fills / etc."

- The **drum MIDI service** (`app/services/drum_midi_service.py`) detects
  onsets with librosa, classifies each hit by spectral centroid + peak
  frequency + envelope, and assigns it to one of 19 GM percussion parts:

  `kick`, `snare`, `sidestick`, `hihat_closed`, `hihat_open`, `tom_high`,
  `tom_himid`, `tom_lomid`, `tom_low`, `tom_floor`, `crash`, `ride`,
  `china`, `splash`, `ride_bell`, `tambourine`, `cowbell`,
  `percussion`, `fill`.

- For every part, a separate `drums_<part>.mid` file is written in
  addition to a combined `drums.mid`. The `fill` part is filled in
  post-hoc by the `_derive_fills` heuristic, which groups temporally
  close bursts into fill candidates.
- The same service writes a sidecar `drums_events.json` with the
  per-hit `{t, note, velocity, part}` payload. The frontend uses this
  to schedule the sample-based player (see Feature 5).

API: per-part files appear in `GET /api/tasks/{id}/stems` as `midi`
kind entries; the event list is served as a static file at
`/storage/outputs/task_<id>/drums_events.json`.

---

## 3. GM / XG bank mapping

> "The generated MIDI should map to GM or XG so it sounds correct in any
> DAW."

- Every MIDI file the worker produces (drums, per-instrument melodic
  parts) is opened with a deterministic setup sequence built by
  `app/services/midi_cc.py::gm_setup_messages`. The sequence is:

  1. **Bank MSB** (CC 0) --- defaults to 0 for GM, 121 for XG.
  2. **Bank LSB** (CC 32) --- defaults to 0.
  3. **Program change** --- instrument-specific, e.g. `24` for guitar.
  4. **Volume** (CC 7) --- 100.
  5. **Expression** (CC 11) --- 127.
  6. **Pan** (CC 10) --- 64 (center).
  7. **Sustain pedal** (CC 64) --- 0 (off).

- The same helper is used for the drum channel (channel 9), where the
  program is unused but the bank MSB / LSB are still set so an XG-aware
  player can select a different kit. The combined drum MIDI includes
  `note_on` / `note_off` pairs on channel 9 only; melodic files use
  distinct channels so they can be routed separately inside a DAW.
- Mapping tables (program number ↔ instrument name) live next to each
  writer --- see `_GM_PROGRAMS` in `basic_pitch_service.py` and the
  per-instrument `program` values in `instrument_classifier_service.py`.

### 3a. Active-SoundFont override (custom keyboards / sample banks)

> "If I have uploaded my own SoundFont presets, the MIDI should use those
> instead of the default GM programs."

- The mapper is called with the current DB session. If
  `SoundFontService.get_active_soundfont(db)` returns an active bank, the
  worker builds a list of `SoundfontOverride` objects via
  `midi_mapping_service.build_soundfont_overrides(preset_infos)`.
- For each non-drum base voice (`piano`, `bass`, `guitar`, `strings`,
  `synth`, `pad`, `lead`, `original`, `vocals`), the override replaces
  the default bank MSB / LSB / program with the matching preset from the
  active SoundFont. Drums are intentionally skipped because
  `sample_library_service` owns the drum mapping (Feature 5).
- The list of applied overrides is serialised into
  `analysis.json` under `soundfont_overrides` and surfaced in two
  places on the frontend:
  - `AudioDetailPage` --- `SoundfontOverridesPanel` shows the voice
    label, stem, bank and program for each override.
  - `SampleLibraryPage` --- `ActiveSoundFontBanner` advertises the
    active bank (or, when none is active, points the user at the
    list to activate one).
- When no SoundFont is active, the worker logs a single debug line
  and the mapper falls back to the default GM voices. The pipeline
  never fails because a SoundFont is missing.

Tests: `backend/tests/test_midi_mapping_service.py` (8 cases).

---

## 4. MIDI controllers (CC) for dynamics & expression

> "Velocity and dynamics controllers should be preserved."

- Each melodic track is written with a complete CC setup at the start
  of the file (Feature 3) and the per-note velocity is taken directly
  from Basic Pitch's note-level `velocity` score (mapped via
  `velocity_from_strength`, a sqrt curve clamped to 35-127).
- **Per-stem expressive CCs** are injected by
  `BasicPitchService._inject_gm_setup` via `_STEM_CC_CONFIG`:
  - CC74 (brightness) --- per-stem, e.g. piano=64, strings=72, synth=80.
  - CC91 (reverb) --- per-stem, e.g. bass=15, vocals=50, strings=55.
  - CC93 (chorus) --- per-stem, e.g. guitar=20, strings=35, synth=25.
  - CC1 (modulation) --- defaults to 0 across all stems.
  Drums deliberately omit CC64/CC74/CC91/CC93 (only CC0/CC32/program/
  CC7/CC11/CC10 are written) since drum channels don't use sustain or
  expression controllers.
- **Pitch bend** is reset to 0 before every note so a slide / bend in
  one track never leaks into the next.
- **Sustain pedal** events are emitted on long sustained chords using
  `sustain_messages(channel, down_at, up_at)` --- a down event at the
  chord start and an up event at the chord end.
- Drum hits carry note velocities derived from the per-onset envelope,
  also mapped through `velocity_from_strength`. The drum channel also
  gets a single CC7/CC10/CC11 setup at the start of the file so the
  kit is mixed at a consistent level across the song.

Tests: `backend/tests/test_midi_cc.py` pins the exact message sequence
produced by `gm_setup_messages` and the curve produced by
`velocity_from_strength`. `test_basic_pitch_service.py` covers
`_normalize_stem_key` and the `_STEM_CC_CONFIG` synth entry.

---

## 5. User-supplied sample library + browser-side playback

> "If I upload my own drum samples, can the app play the transcription
> through my kit?"

- **Database** (`sample_libraries` + `sample_files`, migrations
  `alembic/versions/0004_sample_libraries.py` and
  `0008_sample_velocity_layers.py`): a user can have any number of
  libraries, but only one is `is_active` at a time. The "one active"
  invariant is enforced by a partial unique index. `sample_files`
  carries `velocity_min`/`velocity_max` columns (CHECK constraint
  `velocity_min <= velocity_max`) so multiple samples per note can
  cover different velocity layers.
- **Upload** (`app/api/instruments.py` + `app/services/sample_library_service.py`):
  accepts a `files=` multipart payload (one file per percussion note)
  or a single `zip_file` containing the same. Filenames are mapped to
  GM percussion notes via an alias table (60+ entries: `kick`, `bd`,
  `bass_drum`, `closed_hat`, `hh_closed`, `ride`, `china`, `splash`,
  `cowbell`, etc.). Files with unrecognised names or wrong extensions
  are dropped with a count returned in the response. Per-library
  limits: 5 MB per file, 80 MB total, 80 files, all in `.wav` / `.aiff`
  / `.flac` / `.mp3` / `.ogg`.
- **Velocity layers** (`_resolve_velocity_range`): parses velocity
  ranges from filenames so a single note can have multiple samples
  covering different MIDI velocities. Supported patterns:
  - Dynamic suffixes: `kick_pp.wav`, `snare_ff.wav`
  - Explicit ranges: `kick_vel_001_064.wav`, `snare_vel 065 127.wav`
  - Short-form ranges: `kick_v1-50.wav`, `snare_v_51_100.wav`
  - English labels: `snare_soft.wav`, `crash_hard.wav`
  The `SampleFileInfo` Pydantic schema surfaces `velocity_min`/
  `velocity_max` so the frontend can pick the right layer per incoming
  MIDI velocity.
- **Auto-classification** (`app/services/sample_classifier_service.py`):
  when filename-based lookup fails, samples are classified by 14
  spectral features (centroid, peak frequency, rolloff, RMS envelope,
  band-energy ratios, ZCR, harmonicity, attack ratio) into 36 drum
  types covering kick/snare/hats/toms/cymbals/hand-percussion. The
  classifier is also exposed via `POST /api/instruments/classify` for
  preview before uploading a full library.
- **Activation** (`POST /api/instruments/libraries/{id}/activate`):
  flips the `is_active` bit on the target library and clears it on
  the previous one --- atomic in a single transaction.
- **Browser playback** (`frontend/src/components/SampleBasedDrumPlayer.tsx`):
  - Fetches `drums_events.json` (pre-sorted by `t`) and the active
    library metadata.
  - Decodes each unique sample to an `AudioBuffer`, keyed by
    `${note}:${vMin}:${vMax}` so multiple velocity layers per note
    coexist in the cache.
  - `_pickBuffer` selects the best-matching layer for each incoming
    note+velocity using a 3-tier strategy: exact range match (prefer
    narrowest), full-range fallback, closest layer.
  - Schedules each event with `AudioContext.currentTime` + the event's
    time offset, threading the velocity into a per-hit `GainNode`.
  - Provides Play / Pause / Stop with a `requestAnimationFrame` driven
    playhead.
- The page also surfaces the active library state: if no library is
  active, the player card explains that the GM bank is being used in
  the user's DAW.

API: full CRUD lives under `/api/instruments/...`; a curl-friendly
sample is below.

```bash
# Upload a folder of samples:
curl -X POST http://127.0.0.1:8000/api/instruments/libraries \
  -F "name=Studio kit" \
  -F "description=Recorded 2026" \
  -F "files=@kick.wav" \
  -F "files=@snare.wav" \
  -F "files=@open_hat.wav"

# Activate:
curl -X POST http://127.0.0.1:8000/api/instruments/libraries/1/activate
```

---

## Tests

`cd backend && .venv/bin/pytest` runs the suite (249 tests, all green).
Highlights for Features 1-5:

- `tests/test_drum_midi_service.py` --- per-part MIDI files, GM CC on the
  drum channel, events.json sidecar, classifier coverage.
- `tests/test_midi_cc.py` --- pinned message sequence for the GM setup
  and the velocity curve (single `velocity_from_strength` definition).
- `tests/test_instrument_classifier_service.py` --- full posterior set on
  arbitrary input, short-circuit on silent / very short input, WAV
  output for the active instruments.
- `tests/test_sample_library_service.py` --- filename aliasing, rollback
  on empty / unrecognised payloads, single-active invariant, deletion,
  `velocity_min`/`velocity_max` schema transparency regression, and
  parametrized `_resolve_velocity_range` coverage for `vel_NNN_NNN`,
  `vN-M`, dynamic suffixes, and English labels.
- `tests/test_basic_pitch_service.py` --- `_normalize_stem_key` (strips
  `other_` prefix, falls back to `other`), `_STEM_CC_CONFIG` synth
  entry, GM setup injection per stem.
- `tests/test_music_analysis_service.py` --- BPM / key / chord detection
  on synthetic input.
- `tests/test_task_service.py` / `tests/test_file_service.py` ---
  domain logic for tasks and storage.

---

## 6. User accounts, auth, ownership & quotas

> "Multiple users need to be able to use the app without seeing each
> other's stuff, and the server needs to keep one user from uploading
> the whole internet."

- **Database** --- `users` table + `audio_tasks.user_id` FK
  (`alembic/versions/0005_users.py`). Functional unique indexes on the
  case-folded email and username so a user can sign in with either.
  The migration is reversible.
- **Auth** (`app/services/auth_service.py`) --- bcrypt password hashing
  via passlib + HS256 JWTs with a 24 h access token and a 30 d refresh
  token. Refresh tokens rotate on use, and the same token cannot be
  decoded as the other type (defence in depth).
- **Routes** under `/api/auth/`:
  - `POST /register` --- creates a user and returns a token pair.
  - `POST /login` --- accepts email **or** username.
  - `POST /refresh` --- rotates the refresh token.
  - `GET  /me` --- current user.
  - `POST /logout` --- stateless (the client just discards the tokens).
- **Dependencies** (`app/api/deps.py`) --- `get_current_user` (Bearer
  required) and `get_current_user_optional`. `require_admin` gates
  any future admin-only endpoints. `refresh_settings_check` refuses
  to boot with a placeholder JWT secret in production.
- **Ownership** --- every audio endpoint is scoped: non-admins only see
  their own tasks, and trying to read or delete someone else's task
  returns 403. Legacy tasks with `user_id IS NULL` remain visible to
  everyone (matches the pre-auth behaviour).
- **Quotas** --- `max_tasks` and `max_upload_bytes` per user, with a
  server-wide fallback (`settings.default_max_tasks_per_user` etc.).
  A user that has hit the active-task limit gets a 429 **before** the
  DB write, so the row count never exceeds the limit even on retries.
- **Bootstrap admin** --- first boot seeds an admin from
  `BOOTSTRAP_ADMIN_*` env vars. Idempotent: never overwrites an
  existing user (operator may have rotated the password).
- **Optional gate** --- `AUTH_REQUIRED=false` (the dev default) keeps
  the existing tests + local e2e green without a login dance. Set it
  to `true` in any environment that real users can reach.

---

## 7. Live progress over WebSocket

> "The detail page should show progress the moment the worker finishes
> a step --- not 1.5 s later."

- **Backend** (`app/api/ws.py`) --- `WS /api/ws/tasks/{id}/progress`
  sends an initial `snapshot` from the DB, then relays every
  `task:{id}` pub/sub message. Closes the socket cleanly when the
  task reaches FINISHED / FAILED. Polls the DB every 1 s to catch
  the terminal state even if the worker forgot to publish one.
- **Pub/sub** --- `app/workers/audio_worker.py` publishes
  `{progress, current_step, status, ...}` JSON to `task:{id}` after
  every `_report(...)` call and once more on a fatal exception.
- **Auth** --- token in the `?token=` query param **or** the
  `Authorization` header. Browsers can't reliably send custom
  headers on the WebSocket upgrade, so the query-param form is the
  primary one.
- **Ownership** --- a non-admin watching someone else's task gets a
  `{"type":"error","code":"forbidden"}` event and the server closes
  the socket with code 1008 (no retry storm).
- **Fallback** --- when Redis is unreachable, the handler degrades
  gracefully to DB-only polling so the endpoint still works in
  minimal environments.
- **Frontend** (`frontend/src/hooks/useTaskProgress.ts`) --- opens
  the WebSocket, patches the React Query cache for the detail and
  the list view in place, and reconnects with exponential backoff
  (cap 10 s) on transient failures. Stops retrying after
  1008 / 1000 closes and after a `task_finished` event.
- Polling cadence is now 5 s on the detail page and 8 s on the list
  page --- the WebSocket is the primary live-update path, the poll is
  a safety net.

---

## 8. Health probes, metrics & CORS

> "k8s / nginx / Prometheus need to know if the app is healthy and
> what it's doing."

- `GET /healthz` --- liveness; 200 if the process is up. No DB /
  Redis probe (the process can still serve traffic if Postgres
  briefly hiccups; we just stop accepting new uploads).
- `GET /readyz` --- readiness; 200 only when **both** Postgres and
  Redis respond, 503 otherwise. Body includes per-component status
  so a k8s probe can log exactly what failed.
- `GET /metrics` --- Prometheus text format with:
  - `http_requests_total{method,path,status}` counter
  - `http_request_duration_seconds{method,path,status}` histogram
  - `music_ai_tasks_total{status="UPLOADED|PROCESSING|FINISHED|FAILED"}`
    gauge --- the single most useful number to graph.
- CORS is configured via `CORS_ALLOW_ORIGINS` (comma-separated); the
  default `http://localhost:5173` covers the Vite dev server.

---

## 9. CI / CD

> "Every PR should be checked, and every push to main should produce
> a green check."

- `.github/workflows/ci.yml` --- runs on every PR + push to main.
  Backend: `pip install -r requirements.txt requirements-dev.txt`,
  spins up Redis as a service container, runs `pytest -q`.
  Frontend: `npm ci`, `tsc --noEmit`, `vite build`.
- `.github/workflows/e2e.yml` --- boots Postgres + Redis, runs
  `alembic upgrade head`, starts uvicorn in the background, waits
  for `/healthz`, then runs the Playwright suite. Uploads the
  Playwright HTML report on failure.
- Both workflows share a per-ref concurrency group so a fresh push
  cancels an in-flight run on the same branch.

---

## 10. Deployment

See [`DEPLOY.md`](DEPLOY.md) for the full runbook. Two paths:

- **Compose** for staging / demos (`docker compose up -d`).
- **Bare-metal** (Debian + systemd + nginx + certbot) for production
  deployments that already have a VM fleet.

Both paths expose the same endpoints. The deployment doc also has
a production checklist (JWT secret entropy, AUTH_REQUIRED, CORS,
rate limiting, backups, HSTS) and a first-login runbook.

---

## Tests (after Phase 3)

`cd backend && .venv/bin/pytest` runs the full suite (**249 tests,
all green**). The Phase 1-2 services stay as they were; Phase 3
adds:

- `tests/test_auth_service.py` --- bcrypt round-trip, JWT happy paths,
  type mismatch, tampered signature, expired token, token pair.
- `tests/test_user_service.py` --- registration, normalisation,
  validation, lookup, authentication (email + username), quota
  helpers, active-task count.
- `tests/test_api_auth.py` --- register / login / me / refresh /
  logout over the real HTTP layer, including 401 / 409 / 422 paths.
- `tests/test_api_audio_auth.py` --- auth-required gate, ownership
  403s, scoped list, admin sees all, 429 on quota overflow,
  FINISHED tasks don't count against the quota.
- `tests/test_health.py` --- `/healthz`, `/readyz` body shape,
  Prometheus exposition, CORS preflight.
- `tests/test_ws.py` --- snapshot, terminal FINISHED / FAILED, error
  for missing task, 1008 for forbidden, owner allowed, anonymous
  allowed for legacy tasks.
- `tests/test_midi_mapping_service.py` --- GM default voices,
  SoundFont override replacement, drum-channel skip, bank/program
  plumbing (8 cases).
- `tests/test_soundfont_service.py` --- SF2 parse + active bank
  selection + GM instrument name ↔ program mapping cache.
- `tests/test_adt_drum_service.py` --- onset detection + 19-part
  classification on synthetic transients.
- `tests/test_sample_classifier_service.py` --- 14 spectral features
  -> 36 drum types, confidence thresholds.
- `tests/test_llm_service.py` --- mock + OpenAI-compatible commentary
  providers, prompt shaping, error fallback.
- `tests/test_rate_limit.py` --- per-IP sliding window, auth vs anon
  buckets, 429 semantics.
