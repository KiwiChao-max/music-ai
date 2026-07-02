"""End-to-end for active-SoundFont override in MIDI mapping.

This is the integration test for Feature 3a in FEATURES.md: when a user
imports a custom preset table and activates it, the next audio task
must produce MIDI whose bank / program are remapped to those presets,
and the resulting `analysis.json` must list the overrides.

The script:

  1. Boots the API + a Celery worker pointed at an in-process broker.
  2. POSTs a tiny CSV preset table (one custom piano + one custom bass).
  3. Activates the resulting SoundFont.
  4. Uploads a synthetic C-major arpeggio WAV and triggers processing.
  5. Polls until FINISHED, then downloads `analysis.json` and asserts
     `soundfont_overrides` is non-empty and references both the piano
     and bass presets we imported.
  6. Deactivates (deletes) the SoundFont to leave a clean DB state.

Re-running the script is safe: it reuses an in-process broker and a
temp Celery SQLite file. The script must be run from the repo root.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the WAV synthesiser from the existing e2e_midi script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_midi import write_test_wav  # noqa: E402

BASE = "http://127.0.0.1:8000"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
SAMPLE = PROJECT_ROOT / "scripts" / "_e2e_soundfont.wav"
PRESET_CSV = PROJECT_ROOT / "scripts" / "_e2e_presets.csv"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


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
        ["lsof", "-ti", f":{port}"], check=False, capture_output=True, text=True,
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


def write_preset_csv(path: Path) -> None:
    """Write a minimal CSV with one custom piano and one custom bass."""
    path.write_text(
        "bank_msb,bank_lsb,program,name,category,instrument_type\n"
        "120,0,5,MyCustomPiano,keyboard,piano\n"
        "120,0,34,MyCustomBass,strings,bass\n",
        encoding="utf-8",
    )


def post_multipart(path, fields: dict, file_field: str | None = None) -> tuple[int, bytes]:
    boundary = "----e2e_soundfont"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    if file_field:
        file_path = Path(file_field["path"])
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field["name"]}"; '
            f'filename="{file_path.name}"\r\n'
            f"Content-Type: {file_field.get('content_type', 'text/csv')}\r\n\r\n".encode()
            + file_path.read_bytes()
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return http("POST", path, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def upload_audio(path: Path) -> str:
    boundary = "----e2e_soundfont_audio"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, raw = http(
        "POST", "/api/audio/upload", body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert status in (200, 201), raw
    return json.loads(raw)["task_id"]


# ---- broker + worker ------------------------------------------------------
broker_dir = tempfile.mkdtemp(prefix="celery_broker_soundfont_")
broker_db = os.path.join(broker_dir, "celery.sqlite")
broker_url = f"sqla+sqlite:///{broker_db}"
result_url = "cache+memory://"

print(f"broker:   {broker_url}")

shared_env = {
    **os.environ,
    "CELERY_BROKER_URL": broker_url,
    "CELERY_RESULT_BACKEND": result_url,
    "PYTHONUNBUFFERED": "1",
}

kill_port(8000)
time.sleep(0.5)

api = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app",
     "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
    cwd=BACKEND, env=shared_env,
    stdout=sys.stdout, stderr=subprocess.STDOUT,
)
worker = subprocess.Popen(
    [sys.executable, "-m", "celery", "-A", "app.celery_app:celery",
     "worker", "--loglevel=info", "--concurrency=1", "-P", "solo"],
    cwd=BACKEND, env=shared_env,
    stdout=sys.stdout, stderr=subprocess.STDOUT,
)

try:
    wait_http(f"{BASE}/health", timeout=30)
    time.sleep(2)  # let the worker subscribe to the queue

    # ---- 1) import the preset table -----------------------------------
    write_preset_csv(PRESET_CSV)
    status, body = post_multipart(
        "/api/instruments/preset-table/import",
        {"name": "e2e_keyboard"},
        file_field={"name": "file", "path": str(PRESET_CSV)},
    )
    assert status == 200, body
    sf_info = json.loads(body)
    sf_id = sf_info["id"]
    print(f"imported preset table id={sf_id} presets={len(sf_info.get('presets', []))}")

    # ---- 2) activate it ------------------------------------------------
    status, body = http("POST", f"/api/instruments/soundfonts/{sf_id}/activate")
    assert status == 200, body
    status, body = http("GET", "/api/instruments/soundfonts/active")
    assert status == 200, body
    active = json.loads(body)
    assert active["id"] == sf_id
    print(f"active soundfont: {active['name']}")

    # ---- 3) upload + process a synthetic arpeggio ---------------------
    write_test_wav(SAMPLE)
    task_id = upload_audio(SAMPLE)
    print(f"uploaded task_id={task_id}")

    status, _ = http("POST", f"/api/tasks/{task_id}/process")
    assert status == 202

    snap = None
    for i in range(120):
        time.sleep(0.5)
        status, body = http("GET", f"/api/tasks/{task_id}/status")
        snap = json.loads(body)
        if snap["status"] in ("FINISHED", "FAILED"):
            break
    assert snap and snap["status"] == "FINISHED", snap

    # ---- 4) download + parse analysis.json ----------------------------
    status, body = http("GET", f"/api/tasks/{task_id}/analysis")
    assert status == 200, body
    analysis = json.loads(body)
    overrides = analysis.get("soundfont_overrides") or []
    print(f"soundfont_overrides: {len(overrides)} entries")
    for ov in overrides:
        print(f"  stem={ov.get('stem')}  label={ov.get('label')}  "
              f"bank={ov.get('bank_msb')}:{ov.get('bank_lsb')}  "
              f"program={ov.get('program')}")

    # We imported a piano + bass preset, so at least those two stems
    # must show up as overrides in the analysis JSON.
    override_stems = {ov.get("stem") for ov in overrides}
    assert "piano" in override_stems, f"no piano override: {overrides}"
    assert "bass" in override_stems, f"no bass override: {overrides}"
    piano_ov = next(ov for ov in overrides if ov.get("stem") == "piano")
    assert piano_ov.get("label") == "MyCustomPiano", piano_ov
    assert piano_ov.get("program") == 5
    bass_ov = next(ov for ov in overrides if ov.get("stem") == "bass")
    assert bass_ov.get("label") == "MyCustomBass", bass_ov
    assert bass_ov.get("program") == 34
    print("\nALL CHECKS PASSED")

finally:
    # ---- cleanup: delete the test SoundFont to leave a clean DB -------
    try:
        if "sf_id" in locals():
            http("DELETE", f"/api/instruments/soundfonts/{sf_id}")
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass
    for p in (worker, api):
        try:
            p.terminate()
            p.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            p.kill()
    if broker_dir and os.path.isdir(broker_dir):
        shutil.rmtree(broker_dir, ignore_errors=True)
    SAMPLE.unlink(missing_ok=True)
    PRESET_CSV.unlink(missing_ok=True)
