# FEATURES

The product targets five audio-AI capabilities end-to-end. Each feature is
backed by a backend service, a REST endpoint, a database migration (where
persistent) and a frontend surface. Tests live under
`backend/tests/`.

---

## 1. Instrument-level MIDI transcription

> "Split the drums / bass / guitar / piano / strings / other into editable
> MIDI."

- **Demucs** (`app/services/audio_worker.py`) splits the master into 4 stems.
- The **instrument classifier** (`app/services/instrument_classifier_service.py`)
  runs on the `other` stem and produces per-instrument WAVs for piano,
  guitar, strings, synth, and a residual bucket. Classification uses
  per-frame spectral features (centroid, bandwidth, rolloff, flatness,
  ZCR, HF / low-band ratios) against a small heuristic rule table — no
  external model dependency.
- **Spotify Basic Pitch** (`app/services/basic_pitch_service.py`) transcribes
  each melodic stem into a polyphonic MIDI file. Each output is
  prepended with a full GM setup sequence (see Feature 4).
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

  1. **Bank MSB** (CC 0) — defaults to 0 for GM, 121 for XG.
  2. **Bank LSB** (CC 32) — defaults to 0.
  3. **Program change** — instrument-specific, e.g. `24` for guitar.
  4. **Volume** (CC 7) — 100.
  5. **Expression** (CC 11) — 127.
  6. **Pan** (CC 10) — 64 (center).
  7. **Sustain pedal** (CC 64) — 0 (off).

- The same helper is used for the drum channel (channel 9), where the
  program is unused but the bank MSB / LSB are still set so an XG-aware
  player can select a different kit. The combined drum MIDI includes
  `note_on` / `note_off` pairs on channel 9 only; melodic files use
  distinct channels so they can be routed separately inside a DAW.
- Mapping tables (program number ↔ instrument name) live next to each
  writer — see `_GM_PROGRAMS` in `basic_pitch_service.py` and the
  per-instrument `program` values in `instrument_classifier_service.py`.

---

## 4. MIDI controllers (CC) for dynamics & expression

> "Velocity and dynamics controllers should be preserved."

- Each melodic track is written with a complete CC setup at the start
  of the file (Feature 3) and the per-note velocity is taken directly
  from Basic Pitch's note-level `velocity` score (mapped via
  `velocity_from_strength`, a sqrt curve clamped to 35-127).
- **Pitch bend** is reset to 0 before every note so a slide / bend in
  one track never leaks into the next.
- **Sustain pedal** events are emitted on long sustained chords using
  `sustain_messages(channel, down_at, up_at)` — a down event at the
  chord start and an up event at the chord end.
- Drum hits carry note velocities derived from the per-onset
  on-set envelope, also mapped through `velocity_from_strength`. The
  drum channel also gets a single CC7/CC10/CC11 setup at the start of
  the file so the kit is mixed at a consistent level across the song.

Tests: `backend/tests/test_midi_cc.py` pins the exact message sequence
produced by `gm_setup_messages` and the curve produced by
`velocity_from_strength`.

---

## 5. User-supplied sample library + browser-side playback

> "If I upload my own drum samples, can the app play the transcription
> through my kit?"

- **Database** (`sample_libraries` + `sample_files`, migration
  `alembic/versions/0004_sample_libraries.py`): a user can have any
  number of libraries, but only one is `is_active` at a time. The
  "one active" invariant is enforced by a partial unique index.
- **Upload** (`app/api/instruments.py` + `app/services/sample_library_service.py`):
  accepts a `files=` multipart payload (one file per percussion note)
  or a single `zip_file` containing the same. Filenames are mapped to
  GM percussion notes via an alias table (60+ entries: `kick`, `bd`,
  `bass_drum`, `closed_hat`, `hh_closed`, `ride`, `china`, `splash`,
  `cowbell`, etc.). Files with unrecognised names or wrong extensions
  are dropped with a count returned in the response. Per-library
  limits: 5 MB per file, 80 MB total, 80 files, all in `.wav` / `.aiff`
  / `.flac` / `.mp3` / `.ogg`.
- **Activation** (`POST /api/instruments/libraries/{id}/activate`):
  flips the `is_active` bit on the target library and clears it on
  the previous one — atomic in a single transaction.
- **Browser playback** (`frontend/src/components/SampleBasedDrumPlayer.tsx`):
  - Fetches `drums_events.json` (pre-sorted by `t`) and the active
    library metadata.
  - Decodes each unique sample to an `AudioBuffer`.
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

`cd backend && .venv/bin/pytest` runs the suite (62 tests, all green):

- `tests/test_drum_midi_service.py` — per-part MIDI files, GM CC on the
  drum channel, events.json sidecar, classifier coverage.
- `tests/test_midi_cc.py` — pinned message sequence for the GM setup
  and the velocity curve.
- `tests/test_instrument_classifier_service.py` — full posterior set on
  arbitrary input, short-circuit on silent / very short input, WAV
  output for the active instruments.
- `tests/test_sample_library_service.py` — filename aliasing, rollback
  on empty / unrecognised payloads, single-active invariant, deletion.
- `tests/test_music_analysis_service.py` — BPM / key / chord detection
  on synthetic input.
- `tests/test_task_service.py` / `tests/test_file_service.py` —
  domain logic for tasks and storage.
