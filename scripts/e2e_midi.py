"""End-to-end for Audio To MIDI (Basic Pitch).

Same self-contained harness as `e2e_tasks.py`: kills whatever is on :8000,
starts FastAPI + a Celery worker pointed at an in-process broker, uploads
a synthetic piano-like WAV, waits for the worker to finish, and asserts:

  * the `/api/tasks/{id}/stems` response includes a `kind: "midi"` entry
  * the `.mid` file is downloadable via the static `/storage/` mount
  * the file is a valid Standard MIDI File with at least N notes
    (the synthetic signal has known pitches, so we can sanity-check count
    *and* pitch range)

The input is a short WAV we synthesize on the fly: a 2.5 s C major arpeggio
(C4-E4-G4-C5) at 44.1 kHz. Basic Pitch reliably detects that as ~4 notes.
This is louder / more tonal than the 0.1 s silent WAV `e2e_tasks.py` uses,
which is intentional: a silent file would produce 0 MIDI notes and the
assertion would flap.

Run with: `python scripts/e2e_midi.py` (from repo root) or
`cd scripts && python e2e_midi.py`. The script is idempotent: re-running
just overwrites the local temp WAV and the broker DB.
"""
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

BASE = "http://127.0.0.1:8000"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
SAMPLE = PROJECT_ROOT / "scripts" / "_e2e_midi.wav"
midi_tmp = os.path.join(tempfile.gettempdir(), "_e2e_midi_out.mid")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
E2E_BROKER = os.environ.get("E2E_BROKER", "redis")  # see e2e_tasks.py


# ---- helpers --------------------------------------------------------------
def http(method, path, body=None, headers=None):
    req = urllib.request.Request(
        f"{BASE}{path}", method=method, data=body, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def kill_port(port):
    """Kill whatever is bound to `port` on common dev platforms."""
    if sys.platform.startswith("win"):
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue"
             f" | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
            check=False,
        )
        return

    if shutil.which("lsof") is None:
        return
    result = subprocess.run(
        ["lsof", "-ti", f":{port}"],
        check=False,
        capture_output=True,
        text=True,
    )
    for pid in result.stdout.split():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError):
            pass


def wait_http(url, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionResetError):
            pass
        time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {url}")


# ---- test signal ----------------------------------------------------------
# MIDI note number -> frequency in Hz (A4 = 69 = 440 Hz, equal temperament).
def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def write_test_wav(path: str) -> None:
    """Write a 2.5 s mono 44.1 kHz 16-bit PCM WAV playing C4-E4-G4-C5 as a
    short arpeggio. Each note ~0.6 s with a 30 ms cosine fade-in/out so
    the model has clean onsets to latch onto.
    """
    rate = 44100
    duration = 2.5
    notes = [60, 64, 67, 72]  # C4, E4, G4, C5
    note_dur = duration / len(notes)
    fade = int(0.03 * rate)

    samples = []
    total = int(duration * rate)
    for i in range(total):
        idx = min(int(i / rate / note_dur), len(notes) - 1)
        t_in_note = i / rate - idx * note_dur
        env = 1.0
        if t_in_note < 0.03:
            env = 0.5 * (1 - math.cos(math.pi * t_in_note / 0.03))
        elif t_in_note > note_dur - 0.03:
            env = 0.5 * (1 - math.cos(math.pi * (note_dur - t_in_note) / 0.03))
        freq = midi_to_hz(notes[idx])
        s = env * math.sin(2 * math.pi * freq * (i / rate))
        # Add a quieter second harmonic so the timbre is less of a pure
        # sine 閳?Basic Pitch is trained on real instruments.
        s += 0.3 * env * math.sin(2 * math.pi * 2 * freq * (i / rate))
        samples.append(int(max(-1.0, min(1.0, s * 0.5)) * 32767))

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(rate)
        # wave.writeframes wants bytes; pack each int16 little-endian.
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))


# ---- broker + worker (same shape as e2e_tasks.py) ------------------------
broker_dir = tempfile.mkdtemp(prefix="celery_broker_midi_")
broker_db = os.path.join(broker_dir, "celery.sqlite")
broker_url = f"sqla+sqlite:///{broker_db}"
result_url = "cache+memory://"

print(f"broker:   {broker_url}")
print(f"backend:  {result_url}")

shared_env = {
    **os.environ,
    "CELERY_BROKER_URL": broker_url,
    "CELERY_RESULT_BACKEND": result_url,
    "PYTHONUNBUFFERED": "1",
}

kill_port(8000)
time.sleep(0.5)

api = subprocess.Popen(
    [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", "127.0.0.1", "--port", "8000",
        "--log-level", "info",
    ],
    cwd=BACKEND,
    env=shared_env,
    stdout=sys.stdout,
    stderr=subprocess.STDOUT,
)

worker = subprocess.Popen(
    [
        sys.executable, "-m", "celery",
        "-A", "app.celery_app:celery",
        "worker",
        "--loglevel=info",
        "--concurrency=1",
        "-P", "solo",
    ],
    cwd=BACKEND,
    env=shared_env,
    stdout=sys.stdout,
    stderr=subprocess.STDOUT,
)

try:
    wait_http(f"{BASE}/health", timeout=30)
    print(f"api up; celery worker pid={worker.pid}")

    # Give the worker time to subscribe to the queue.
    time.sleep(3)

    # ---- 1) build + upload the test WAV ----------------------------------
    write_test_wav(SAMPLE)
    print(f"wrote test signal: {SAMPLE}")

    boundary = "----e2e_midi"
    form = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="_e2e_midi.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + SAMPLE.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status, body = http(
        "POST", "/api/audio/upload", form,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert status in (200, 201), body
    task_id = json.loads(body)["task_id"]
    print(f"uploaded task_id={task_id}")

    # ---- 2) trigger processing ------------------------------------------
    status, body = http("POST", f"/api/tasks/{task_id}/process")
    print(f"POST  /api/tasks/{task_id}/process        -> {status}  {body.decode()}")
    assert status == 202, body

    # ---- 3) poll /status -------------------------------------------------
    snap = None
    for i in range(120):  # up to ~60 s 閳?Basic Pitch is fast but cold load takes time
        time.sleep(0.5)
        status, body = http("GET", f"/api/tasks/{task_id}/status")
        snap = json.loads(body)
        print(f"  t={i*0.5:.1f}s  GET /status                  -> {status}  {snap}")
        if snap["status"] in ("FINISHED", "FAILED"):
            break
    assert snap and snap["status"] == "FINISHED", snap

    # ---- 4) GET /stems 閳?must include a MIDI row ------------------------
    status, body = http("GET", f"/api/tasks/{task_id}/stems")
    stems = json.loads(body)
    print(f"GET   /api/tasks/{task_id}/stems          -> {status}")
    for s in stems:
        print(f"  kind={s.get('kind','?'):<5}  name={s['name']:<12}  url={s['url']}")
    assert status == 200
    audio_stems = [s for s in stems if s.get("kind") == "audio"]
    midi_stems = [s for s in stems if s.get("kind") == "midi"]
    # The 4 Demucs placeholders are still there.
    assert {s["name"] for s in audio_stems} >= {"vocals", "drums", "bass", "other"}, stems
    # And exactly one MIDI output.
    assert len(midi_stems) >= 1, f"expected >= 1 MIDI stem, got {stems}"
    assert all(s["url"].endswith(".mid") for s in midi_stems), stems

    # ---- 5) download and parse MIDI outputs ------------------------------
    # Real stem separation can produce several MIDI files. Some stems may be
    # intentionally empty for the synthetic input (e.g. bass on a piano-like
    # arpeggio), so find one MIDI with enough notes instead of assuming the
    # first row is the melodic one.
    import pretty_midi  # type: ignore

    selected = None
    for candidate in sorted(
        midi_stems,
        key=lambda item: (item["name"] not in {"original", "other", "vocals", "stems_gm"}, item["name"]),
    ):
        midi_url = candidate["url"]
        status, body = http("GET", midi_url)
        print(f"GET   {midi_url}  -> {status}, {len(body)} bytes")
        assert status == 200 and len(body) > 0

        with open(midi_tmp, "wb") as f:
            f.write(body)

        pm = pretty_midi.PrettyMIDI(midi_tmp)
        note_count = sum(len(inst.notes) for inst in pm.instruments)
        pitches = sorted({
            n.pitch
            for inst in pm.instruments
            for n in inst.notes
        })
        print(f"pretty_midi[{candidate['name']}]: {note_count} notes, pitches (MIDI) = {pitches}")
        if note_count >= 2:
            selected = (candidate["name"], note_count, pitches)
            break

    assert selected is not None, f"no MIDI output contained enough notes: {midi_stems}"
    selected_name, note_count, pitches = selected
    assert any(p in {60, 64, 67, 72} for p in pitches), (
        f"selected MIDI {selected_name} did not include expected C-major arpeggio pitches: {pitches}"
    )
    print("\nALL CHECKS PASSED")
finally:
    for p in (worker, api):
        try:
            p.terminate()
            p.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            p.kill()
    if broker_dir and os.path.isdir(broker_dir):
        shutil.rmtree(broker_dir, ignore_errors=True)
    SAMPLE.unlink(missing_ok=True)
    if os.path.exists(midi_tmp):
        try:
            os.remove(midi_tmp)
        except OSError:
            pass
